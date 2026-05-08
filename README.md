# Stock Editorial Agent

一个针对单只股票的社论 + 行情联合分析 Agent（`uv` 管理依赖，支持 OpenAI / DeepSeek）。

## 能力
- 读取多篇社论文本（`.txt`）
- 免费拉取东证股票行情（`yfinance`）
- 融合“叙事 + 价格行为”生成结构化研判
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

3. 运行示例（免费行情 + DeepSeek + 东证 7203）
```powershell
uv run stock-editorial-agent --provider deepseek --price-source yfinance --ticker 7203 --input examples --from-date 2026-01-01 --to-date 2026-05-01 --out outputs
```

## 参数说明
- `--provider`: `openai` 或 `deepseek`
- `--model`: 可选，覆盖默认模型
- `--price-source`: `yfinance`（免费）或 `jquants`
- `--ticker`: 股票代码（东证示例 `7203` 或 `7203.T`）
- `--input`: 社论文本文件或目录
- `--from-date` / `--to-date`: 行情区间（YYYY-MM-DD）
- `--out`: 输出目录

## 输出
- `outputs/<TICKER>_<timestamp>.json`
- `outputs/<TICKER>_<timestamp>.md`

## 说明
- `yfinance` 是非官方 Yahoo 接口封装，免费但稳定性与可用条款不等同交易所官方 API。
- 如需官方数据可切换 `--price-source jquants`。
