from __future__ import annotations

import argparse
import json
import os
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yfinance as yf
from dotenv import load_dotenv
from openai import OpenAI

SYSTEM_PROMPT = """You are a disciplined equity editorial analyst.
You analyze one stock using BOTH:
1) editorial/opinion corpus
2) recent market price behavior

Rules:
- Do not fabricate facts that are absent from inputs.
- Clearly separate facts vs inferences.
- If editorial narrative conflicts with price action, explicitly discuss divergence.
- Output valid JSON only following the schema.

JSON schema:
{
  "ticker": "string",
  "overall_stance": "Bullish|Neutral|Bearish|Mixed",
  "confidence": 0-100,
  "sentiment_score": -100 to 100,
  "price_action_bias": "Bullish|Neutral|Bearish|Mixed",
  "narrative_price_alignment": "Aligned|Partially Aligned|Divergent",
  "key_thesis": ["string", "..."],
  "major_risks": ["string", "..."],
  "upside_catalysts": ["string", "..."],
  "downside_triggers": ["string", "..."],
  "signal_quality": "High|Medium|Low",
  "time_horizon": "Near-term|Medium-term|Long-term|Mixed",
  "fact_vs_inference": {
     "facts_from_editorials": ["string", "..."],
     "facts_from_market_data": ["string", "..."],
     "inferences": ["string", "..."]
  },
  "watchlist_metrics": ["string", "..."],
  "one_sentence_take": "string"
}
"""

USER_TEMPLATE = """Target ticker: {ticker}

Editorial corpus:
{editorials}

Recent market data summary (JSON):
{market_summary}

Return JSON only.
"""


@dataclass
class AnalysisResult:
    raw_json: dict

    def to_markdown(self) -> str:
        d = self.raw_json
        lines = [
            f"# {d.get('ticker', 'N/A')} 社论+行情分析报告",
            "",
            f"- 总体立场: **{d.get('overall_stance', 'N/A')}**",
            f"- 置信度: **{d.get('confidence', 'N/A')} / 100**",
            f"- 情绪分数: **{d.get('sentiment_score', 'N/A')}** (-100 到 100)",
            f"- 价格行为偏向: **{d.get('price_action_bias', 'N/A')}**",
            f"- 叙事-价格一致性: **{d.get('narrative_price_alignment', 'N/A')}**",
            f"- 信号质量: **{d.get('signal_quality', 'N/A')}**",
            f"- 时间维度: **{d.get('time_horizon', 'N/A')}**",
            "",
            "## 核心逻辑",
        ]

        for item in d.get("key_thesis", []):
            lines.append(f"- {item}")

        lines.append("\n## 主要风险")
        for item in d.get("major_risks", []):
            lines.append(f"- {item}")

        lines.append("\n## 上行催化剂")
        for item in d.get("upside_catalysts", []):
            lines.append(f"- {item}")

        lines.append("\n## 下行触发因素")
        for item in d.get("downside_triggers", []):
            lines.append(f"- {item}")

        fvi = d.get("fact_vs_inference", {})
        lines.append("\n## 事实 vs 推断")
        lines.append("### 来自社论的事实")
        for item in fvi.get("facts_from_editorials", []):
            lines.append(f"- {item}")
        lines.append("### 来自行情的事实")
        for item in fvi.get("facts_from_market_data", []):
            lines.append(f"- {item}")
        lines.append("### 推断")
        for item in fvi.get("inferences", []):
            lines.append(f"- {item}")

        lines.append("\n## 重点跟踪指标")
        for item in d.get("watchlist_metrics", []):
            lines.append(f"- {item}")

        lines.append("\n## 一句话结论")
        lines.append(d.get("one_sentence_take", "N/A"))

        return "\n".join(lines)


class MarketDataError(RuntimeError):
    pass


def load_editorials(path: Path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8")

    if path.is_dir():
        texts: List[str] = []
        for fp in sorted(path.glob("*.txt")):
            texts.append(f"=== {fp.name} ===\n{fp.read_text(encoding='utf-8')}")
        if not texts:
            raise ValueError(f"No .txt files found in {path}")
        return "\n\n".join(texts)

    raise ValueError(f"Input path not found: {path}")


def normalize_tse_code(ticker: str) -> str:
    code = ticker.strip().upper().replace(".T", "")
    if not code.isdigit():
        return ticker.upper()
    return f"{code}0"


def make_market_summary(price_rows: List[Dict[str, Any]], ticker: str) -> Dict[str, Any]:
    if not price_rows:
        raise MarketDataError("No price rows returned from market API.")

    close_key = "Close"
    date_key = "Date"

    closes: List[float] = []
    dated_rows: List[tuple[str, Dict[str, Any]]] = []

    for row in price_rows:
        try:
            dt = str(row.get(date_key, ""))
            c = float(row.get(close_key))
        except (TypeError, ValueError):
            continue
        dated_rows.append((dt, row))
        closes.append(c)

    if len(closes) < 2:
        raise MarketDataError("Insufficient close prices for analysis.")

    dated_rows.sort(key=lambda x: x[0])
    first_close = float(dated_rows[0][1][close_key])
    last_close = float(dated_rows[-1][1][close_key])
    pct_change = ((last_close - first_close) / first_close) * 100.0

    returns = []
    for i in range(1, len(closes)):
        prev_c = closes[i - 1]
        curr_c = closes[i]
        if prev_c != 0:
            returns.append((curr_c - prev_c) / prev_c)

    volatility = statistics.pstdev(returns) * 100.0 if returns else 0.0

    return {
        "ticker": ticker,
        "points": len(dated_rows),
        "start_date": dated_rows[0][0],
        "end_date": dated_rows[-1][0],
        "start_close": round(first_close, 4),
        "end_close": round(last_close, 4),
        "period_return_pct": round(pct_change, 3),
        "daily_volatility_pct": round(volatility, 3),
        "latest_row": dated_rows[-1][1],
    }


def fetch_jquants_prices(ticker: str, from_date: str, to_date: str) -> List[Dict[str, Any]]:
    base_url = os.getenv("JQUANTS_BASE_URL", "https://api.jquants.com/v1").rstrip("/")
    prices_path = os.getenv("JQUANTS_PRICES_PATH", "/prices/daily_quotes")
    url = f"{base_url}{prices_path}"

    api_key = os.getenv("JQUANTS_API_KEY", "").strip()
    bearer = os.getenv("JQUANTS_BEARER_TOKEN", "").strip()
    if not api_key and not bearer:
        raise MarketDataError("Set JQUANTS_API_KEY or JQUANTS_BEARER_TOKEN in .env")

    headers: Dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    params = {
        "code": normalize_tse_code(ticker),
        "from": from_date,
        "to": to_date,
    }

    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code >= 400:
        raise MarketDataError(f"J-Quants API error {resp.status_code}: {resp.text[:400]}")

    payload = resp.json()
    rows = payload.get("daily_quotes") or payload.get("prices") or payload.get("data") or []
    if not isinstance(rows, list):
        raise MarketDataError("Unexpected J-Quants response format.")
    return rows


def normalize_yahoo_jp_ticker(ticker: str) -> str:
    t = ticker.strip().upper()
    if t.endswith(".T"):
        return t
    if t.isdigit():
        return f"{t}.T"
    return t


def fetch_yfinance_prices(ticker: str, from_date: str, to_date: str) -> List[Dict[str, Any]]:
    symbol = normalize_yahoo_jp_ticker(ticker)
    df = yf.download(symbol, start=from_date, end=to_date, progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise MarketDataError(f"No yfinance data for {symbol} in {from_date}..{to_date}")

    rows: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        rows.append(
            {
                "Date": idx.strftime("%Y-%m-%d"),
                "Open": float(row["Open"]),
                "High": float(row["High"]),
                "Low": float(row["Low"]),
                "Close": float(row["Close"]),
                "Volume": float(row["Volume"]) if row.get("Volume") is not None else None,
            }
        )
    return rows


def build_llm_client(provider: str) -> tuple[OpenAI, str]:
    provider = provider.lower()

    if provider == "openai":
        model = os.getenv("OPENAI_MODEL", os.getenv("MODEL", "gpt-5.4-mini"))
        return OpenAI(api_key=os.getenv("OPENAI_API_KEY")), model

    if provider == "deepseek":
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        return OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url=base_url), model

    raise ValueError("--provider must be one of: openai, deepseek")


def analyze(
    provider: str,
    model: Optional[str],
    ticker: str,
    editorial_text: str,
    market_summary: Dict[str, Any],
) -> AnalysisResult:
    client, inferred_model = build_llm_client(provider)
    final_model = model or inferred_model

    response = client.responses.create(
        model=final_model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_TEMPLATE.format(
                    ticker=ticker.upper(),
                    editorials=editorial_text,
                    market_summary=json.dumps(market_summary, ensure_ascii=False),
                ),
            },
        ],
    )

    raw_text = response.output_text.strip()
    parsed = json.loads(raw_text)
    return AnalysisResult(raw_json=parsed)


def save_outputs(result: AnalysisResult, out_dir: Path, ticker: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"{ticker.upper()}_{ts}.json"
    md_path = out_dir / f"{ticker.upper()}_{ts}.md"

    json_path.write_text(
        json.dumps(result.raw_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(result.to_markdown(), encoding="utf-8")
    return json_path, md_path


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Single-stock editorial + market analysis agent")
    parser.add_argument("--ticker", required=True, help="Target ticker. JP example: 7203 or 7203.T")
    parser.add_argument(
        "--input",
        required=True,
        help="Path to one .txt file or a directory containing multiple .txt editorials",
    )
    parser.add_argument("--provider", default=os.getenv("LLM_PROVIDER", "openai"), help="openai or deepseek")
    parser.add_argument("--model", default=None, help="Optional override model name")
    parser.add_argument(
        "--price-source",
        default=os.getenv("PRICE_SOURCE", "yfinance"),
        help="yfinance (free) or jquants",
    )
    parser.add_argument("--from-date", required=True, help="Market data start date, e.g. 2026-01-01")
    parser.add_argument("--to-date", required=True, help="Market data end date, e.g. 2026-05-01")
    parser.add_argument("--out", default="outputs")
    args = parser.parse_args()

    editorial_text = load_editorials(Path(args.input))
    if args.price_source.lower() == "yfinance":
        price_rows = fetch_yfinance_prices(args.ticker, args.from_date, args.to_date)
    elif args.price_source.lower() == "jquants":
        price_rows = fetch_jquants_prices(args.ticker, args.from_date, args.to_date)
    else:
        raise ValueError("--price-source must be one of: yfinance, jquants")
    market_summary = make_market_summary(price_rows, args.ticker.upper())

    result = analyze(
        provider=args.provider,
        model=args.model,
        ticker=args.ticker,
        editorial_text=editorial_text,
        market_summary=market_summary,
    )
    json_path, md_path = save_outputs(result, Path(args.out), args.ticker)

    print(f"Done. JSON: {json_path}")
    print(f"Done. Markdown: {md_path}")


if __name__ == "__main__":
    main()
