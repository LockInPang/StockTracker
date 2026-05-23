from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote_plus, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}


@dataclass
class EditorialArticle:
    ticker: str
    company_name: str
    source: str
    title: str
    published_at: str
    url: str
    body: str


def build_google_news_rss_url(ticker: str, company_name: str, language: str) -> str:
    search_terms = company_name.strip() or ticker.strip()
    if language == "ja":
        query = f"{search_terms} {ticker} 株価 OR 決算 OR 見通し"
        return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ja&gl=JP&ceid=JP:ja"
    query = f"{search_terms} {ticker} stock OR earnings OR outlook"
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"


def fetch_rss_article_urls(rss_urls: Iterable[str], limit: int) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()

    for rss_url in rss_urls:
        feed = feedparser.parse(rss_url)
        for entry in feed.entries:
            link = str(getattr(entry, "link", "")).strip()
            if not link or link in seen:
                continue
            source = getattr(entry, "source", {}) or {}
            summary_html = str(getattr(entry, "summary", "")).strip()
            summary = BeautifulSoup(summary_html, "html.parser").get_text(" ", strip=True)
            seen.add(link)
            items.append(
                {
                    "url": link,
                    "title": str(getattr(entry, "title", "")).strip(),
                    "published_at": str(
                        getattr(entry, "published", "")
                        or getattr(entry, "updated", "")
                        or ""
                    ).strip(),
                    "summary": summary,
                    "source": str(source.get("title", "") or source.get("href", "")).strip(),
                }
            )
            if len(items) >= limit:
                return items

    return items


def fetch_html(url: str, timeout: int) -> str:
    resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def extract_article_body(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    candidates = soup.select("article, main, [role='main']")
    if not candidates:
        candidates = [soup.body or soup]

    best_text = ""
    for candidate in candidates:
        paragraphs = [
            p.get_text(" ", strip=True)
            for p in candidate.find_all(["p", "li"])
            if p.get_text(" ", strip=True)
        ]
        text = "\n".join(paragraphs).strip()
        if len(text) > len(best_text):
            best_text = text

    return title, normalize_whitespace(best_text)


def normalize_whitespace(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def source_name(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def slugify_filename(value: str, fallback: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]+", "_", value).strip("_")
    slug = slug[:60]
    return slug or fallback


def fetch_article(
    ticker: str,
    company_name: str,
    item: dict[str, str],
    timeout: int,
    min_chars: int,
) -> Optional[EditorialArticle]:
    url = item["url"]
    try:
        html = fetch_html(url, timeout)
        page_title, body = extract_article_body(html)
    except Exception as e:
        print(f"Skip failed article: {url} ({e})")
        return None

    title = item.get("title") or page_title or url
    published_at = item.get("published_at") or ""
    if len(body) < min_chars:
        summary = item.get("summary", "").strip()
        if len(summary) >= min_chars:
            body = f"RSS 摘要（未能从原文页抽取足够正文）:\n{summary}"
        else:
            body = f"RSS 条目（未能从原文页抽取足够正文）:\n标题: {title}"

    if len(body) < min_chars:
        print(f"Skip short article: {url}")
        return None

    return EditorialArticle(
        ticker=ticker,
        company_name=company_name,
        source=item.get("source") or source_name(url),
        title=title,
        published_at=published_at,
        url=url,
        body=body,
    )


def write_article(article: EditorialArticle, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(article.url.encode("utf-8")).hexdigest()[:8]
    date_prefix = datetime.now().strftime("%Y%m%d")
    source_slug = slugify_filename(article.source, "source")
    title_slug = slugify_filename(article.title, "article")
    path = out_dir / f"{date_prefix}_{source_slug}_{title_slug}_{digest}.txt"

    text = "\n".join(
        [
            f"股票代码: {article.ticker}",
            f"公司名称: {article.company_name}",
            f"来源: {article.source}",
            f"标题: {article.title}",
            f"发布时间: {article.published_at or 'N/A'}",
            f"URL: {article.url}",
            "",
            article.body,
            "",
        ]
    )
    path.write_text(text, encoding="utf-8")
    return path


def write_manifest(articles: list[EditorialArticle], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "manifest.json"
    path.write_text(
        json.dumps([asdict(article) for article in articles], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Fetch real editorial/news articles for stock analysis")
    parser.add_argument("--ticker", required=True, help="Target ticker, e.g. 5591")
    parser.add_argument("--company-name", default="", help="Company name for search query, e.g. AVILEN")
    parser.add_argument("--rss", action="append", default=[], help="RSS feed URL. Can be provided multiple times.")
    parser.add_argument("--url", action="append", default=[], help="Article URL. Can be provided multiple times.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of saved articles")
    parser.add_argument("--language", choices=["ja", "en"], default="ja", help="Default search language")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds")
    parser.add_argument("--min-chars", type=int, default=30, help="Minimum extracted body length")
    parser.add_argument("--out", default=None, help="Output directory. Defaults to data/editorials/<ticker>")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else Path("data") / "editorials" / args.ticker
    rss_urls = list(args.rss)
    if not rss_urls and not args.url:
        rss_urls.append(build_google_news_rss_url(args.ticker, args.company_name, args.language))

    items = [{"url": url, "title": "", "published_at": ""} for url in args.url]
    remaining = max(args.limit - len(items), 0)
    if remaining:
        items.extend(fetch_rss_article_urls(rss_urls, max(remaining * 3, remaining)))

    articles: list[EditorialArticle] = []
    seen_urls: set[str] = set()
    for item in items:
        if len(articles) >= args.limit:
            break
        url = item["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        article = fetch_article(args.ticker, args.company_name, item, args.timeout, args.min_chars)
        if not article:
            continue
        path = write_article(article, out_dir)
        articles.append(article)
        print(f"Saved: {path}")

    manifest_path = write_manifest(articles, out_dir)
    print(f"Saved manifest: {manifest_path}")
    print(f"Fetched articles: {len(articles)}")
    if not articles:
        raise SystemExit("No usable articles were fetched.")


if __name__ == "__main__":
    main()
