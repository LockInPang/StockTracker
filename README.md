# Stock Editorial Agent

一个针对单只股票的社论 + 行情联合分析 Agent（`uv` 管理依赖，支持 OpenAI / DeepSeek）。

## 能力
- 抓取真实社论/新闻文章（RSS、指定 URL、默认 Google News RSS 搜索）
- 抓取 Yahoo!ファイナンス掲示板下的股票评论
- 读取多篇社论文本（`.txt`）
- 免费拉取东证股票行情（`yfinance`）
- 融合“社论叙事 + 投资者评论 + 价格行为”生成结构化研判
- 输出 JSON 与 Markdown 报告

## 快速开始（uv）
1. 安装依赖
```bash
uv sync
```

2. 配置密钥
```bash
copy .env.example .env
```
至少填写：
- `DEEPSEEK_API_KEY`（若用 DeepSeek）或 `OPENAI_API_KEY`

3. 抓取真实社论/新闻文章（示例：5591 / AVILEN）
```powershell
uv run python -m src.fetch_editorials --ticker 5591 --company-name AVILEN --limit 5 --out data/editorials/5591
```

也可以手动指定 RSS 或文章 URL：
```powershell
uv run python -m src.fetch_editorials --ticker 5591 --company-name AVILEN --rss "https://example.com/feed.xml" --limit 5 --out data/editorials/5591

uv run python -m src.fetch_editorials --ticker 5591 --company-name AVILEN --url "https://example.com/article.html" --out data/editorials/5591
```

4. 抓取 Yahoo!ファイナンス 掲示板评论
```powershell
uv run python -m src.fetch_yahoo_comments --ticker 5591 --limit 50 --out data/comments/5591
```

5. 运行完整分析（真实社论 + Yahoo 评论 + 免费行情 + DeepSeek）
```powershell
uv run python -m src.analyze --provider deepseek --price-source auto --ticker 5591 --input data/editorials/5591 --comments-input data/comments/5591 --from-date 2026-01-01 --to-date 2026-05-01 --out outputs
```

## 参数说明
### 抓取社论
- `--ticker`: 股票代码（例如 `5591`）
- `--company-name`: 公司名，用于默认新闻搜索（例如 `AVILEN`）
- `--rss`: RSS 源地址，可重复传入
- `--url`: 文章 URL，可重复传入
- `--limit`: 最多保存文章数
- `--language`: 默认搜索语言，`ja` 或 `en`
- `--min-chars`: 最短正文/摘要长度，默认 `30`
- `--out`: 社论输出目录，默认 `data/editorials/<ticker>`

### 抓取 Yahoo 评论
- `--ticker`: 股票代码（例如 `5591` 或 `5591.T`）
- `--limit`: 最多保存评论数
- `--timeout`: HTTP 超时时间
- `--out`: 评论输出目录，默认 `data/comments/<ticker>`

### 分析报告
- `--provider`: `openai` 或 `deepseek`
- `--model`: 可选，覆盖默认模型
- `--price-source`: `yfinance`（免费）、`jquants` 或 `auto`
- `--ticker`: 股票代码（东证示例 `7203` 或 `7203.T`）
- `--input`: 社论文本文件或目录
- `--comments-input`: 可选，投资者评论文本文件或目录
- `--from-date` / `--to-date`: 行情区间（YYYY-MM-DD）
- `--out`: 输出目录

## 输出
- `data/editorials/<TICKER>/*.txt`
- `data/editorials/<TICKER>/manifest.json`
- `data/comments/<TICKER>/*.txt`
- `data/comments/<TICKER>/manifest.json`
- `outputs/<TICKER>_<timestamp>.json`
- `outputs/<TICKER>_<timestamp>.md`

## 说明
- `yfinance` 是非官方 Yahoo 接口封装，免费但稳定性与可用条款不等同交易所官方 API。
- 如需官方数据可切换 `--price-source jquants`。
- 抓取网页时请遵守目标网站的 robots.txt、服务条款与访问频率限制；若要长期稳定使用，建议优先接入授权新闻/API 数据源。
- Yahoo 掲示板评论只适合作为投资者情绪和分歧度参考，不应作为事实来源。
