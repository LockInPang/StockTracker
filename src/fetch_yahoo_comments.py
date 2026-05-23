from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}


@dataclass
class YahooComment:
    ticker: str
    source: str
    comment_no: str
    author: str
    posted_at: str
    body: str
    url: str


def normalize_yahoo_jp_ticker(ticker: str) -> str:
    t = ticker.strip().upper()
    if t.endswith(".T"):
        return t
    if t.isdigit():
        return f"{t}.T"
    return t


def forum_url(ticker: str) -> str:
    return f"https://finance.yahoo.co.jp/quote/{normalize_yahoo_jp_ticker(ticker)}/forum"


def fetch_html(url: str, timeout: int) -> str:
    resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def visible_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        tag.decompose()
    return [line for line in (clean_line(s) for s in soup.get_text("\n").splitlines()) if line]


def parse_helpful_votes(lines: list[str], start_index: int) -> tuple[Optional[int], Optional[int]]:
    yes: Optional[int] = None
    no: Optional[int] = None
    for idx, line in enumerate(lines[start_index : start_index + 12], start=start_index):
        yes_match = re.match(r"はい\s+(\d+)", line)
        no_match = re.match(r"いいえ\s+(\d+)", line)
        if yes_match:
            yes = int(yes_match.group(1))
        if no_match:
            no = int(no_match.group(1))
        if line == "はい" and idx + 1 < len(lines) and lines[idx + 1].isdigit():
            yes = int(lines[idx + 1])
        if line == "いいえ" and idx + 1 < len(lines) and lines[idx + 1].isdigit():
            no = int(lines[idx + 1])
    return yes, no


def parse_comments(html: str, ticker: str, url: str, limit: int) -> list[YahooComment]:
    lines = visible_lines(html)
    comments: list[YahooComment] = []
    marker = re.compile(r"^No\.\s*(\d+)\s+(\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2})\s+報告$")
    split_date = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}$")

    for index, line in enumerate(lines):
        match = marker.match(line)
        if match:
            comment_no = match.group(1)
            posted_at = match.group(2)
            body_start = index + 1
        elif (
            line == "No."
            and index + 3 < len(lines)
            and lines[index + 1].isdigit()
            and split_date.match(lines[index + 2])
            and lines[index + 3] == "報告"
        ):
            comment_no = lines[index + 1]
            posted_at = lines[index + 2]
            body_start = index + 4
        else:
            continue

        author = lines[index - 1] if index > 0 else ""
        body_lines: list[str] = []
        cursor = body_start
        while cursor < len(lines):
            current = lines[cursor]
            if marker.match(current) or current == "No.":
                break
            if current in {"返信", "投資の参考になりましたか？", "報告"}:
                break
            if re.match(r"^(はい|いいえ)\s+\d+$", current):
                break
            if current.startswith(">>"):
                cursor += 1
                continue
            body_lines.append(current)
            cursor += 1

        body = "\n".join(body_lines).strip()
        if not body:
            continue

        comments.append(
            YahooComment(
                ticker=ticker,
                source="Yahoo!ファイナンス掲示板",
                comment_no=comment_no,
                author=author,
                posted_at=posted_at,
                body=body,
                url=url,
            )
        )
        if len(comments) >= limit:
            break

    return comments


def write_comments(comments: list[YahooComment], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1("".join(c.comment_no for c in comments).encode("utf-8")).hexdigest()[:8]
    date_prefix = datetime.now().strftime("%Y%m%d")
    path = out_dir / f"{date_prefix}_yahoo_comments_{digest}.txt"

    blocks: list[str] = []
    for comment in comments:
        blocks.append(
            "\n".join(
                [
                    f"股票代码: {comment.ticker}",
                    f"来源: {comment.source}",
                    f"评论编号: {comment.comment_no}",
                    f"作者: {comment.author}",
                    f"发布时间: {comment.posted_at}",
                    f"URL: {comment.url}",
                    "",
                    comment.body,
                ]
            )
        )

    path.write_text("\n\n---\n\n".join(blocks) + "\n", encoding="utf-8")
    return path


def write_manifest(comments: list[YahooComment], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "manifest.json"
    path.write_text(
        json.dumps([asdict(comment) for comment in comments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Fetch Yahoo Finance Japan forum comments")
    parser.add_argument("--ticker", required=True, help="Target ticker, e.g. 5591 or 5591.T")
    parser.add_argument("--limit", type=int, default=50, help="Maximum number of comments to save")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds")
    parser.add_argument("--out", default=None, help="Output directory. Defaults to data/comments/<ticker>")
    args = parser.parse_args()

    ticker = normalize_yahoo_jp_ticker(args.ticker)
    url = forum_url(args.ticker)
    html = fetch_html(url, args.timeout)
    comments = parse_comments(html, ticker, url, args.limit)
    if not comments:
        raise SystemExit(f"No comments were parsed from {url}")

    out_dir = Path(args.out) if args.out else Path("data") / "comments" / args.ticker.replace(".T", "")
    comments_path = write_comments(comments, out_dir)
    manifest_path = write_manifest(comments, out_dir)
    print(f"Saved comments: {comments_path}")
    print(f"Saved manifest: {manifest_path}")
    print(f"Fetched comments: {len(comments)}")


if __name__ == "__main__":
    main()
