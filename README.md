# TrendRadar AI Pipeline

**TrendRadar AI 分析管道** — 从 TrendRadar MCP 拉取热点数据，经百炼 AI 做结构化分析，并投递到 xiaohongshu-mcp，用于自动生成/发布小红书文案。

A scheduled pipeline: **TrendRadar MCP → 百炼 AI 分析 → xiaohongshu-mcp (HTTP ingest)**.

---

## 特性

- 定时从 TrendRadar MCP 拉取最新热点（可配置条数、间隔或 cron）
- 百炼 AI（华北2北京兼容接口）结构化分析：关键信息、情感、主题、摘要
- HTTP 投递到 xiaohongshu-mcp，支持单条/批量与重试队列
- 配置与密钥分离：敏感信息仅通过环境变量，不写进仓库

---

## 目录

- [数据流](#数据流)
- [环境要求与安装](#环境要求与安装)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [环境变量](#环境变量)
- [与 xiaohongshu-mcp 的对接约定](#与-xiaohongshu-mcp-的对接约定)
- [Docker 部署](#docker-部署)
- [错误与重试](#错误与重试)
- [安全提示](#安全提示)
- [项目结构](#项目结构)
- [贡献与许可证](#贡献与许可证)

---

## 数据流

```
TrendRadar MCP (get_latest_news) → 百炼 AI 分析 → xiaohongshu-mcp (POST /api/ingest)
```

---

## 环境要求与安装

- **Python** 3.10+
- **TrendRadar MCP** 已启动（HTTP 模式，默认 `http://host:3333/mcp`）
- **百炼 API Key**（华北2北京兼容接口）
- **xiaohongshu-mcp** 接收端可访问

安装依赖（推荐 [uv](https://github.com/astral-sh/uv)）：

```bash
uv sync
# 或
pip install -e .
```

依赖定义见 [pyproject.toml](pyproject.toml)。

---

## 快速开始

1. **复制环境变量模板并填入必填项**（不要提交 `.env` 到仓库）：

   ```bash
   cp .env.example .env
   # 编辑 .env，至少设置 BAILIAN_API_KEY=your_key
   ```

2. **按需编辑** `config/config.yaml`（MCP 地址、下游 URL、调度间隔等）；敏感项使用 `env:VAR_NAME`，见 [配置说明](#配置说明)。

3. **启动管道**（先执行一次，再按间隔定时运行）：

   ```bash
   uv run python -m pipeline.main
   # 或
   python -m pipeline.main
   ```

### 只跑一次验证

在项目根目录执行 `uv run python -m pipeline.main`，等首次管道执行完成后按 `Ctrl+C` 退出即可。

根据日志判断环节是否正常：

- **「MCP 拉取成功」** 且有条数、首条标题摘要 → MCP 正常
- **「百炼分析完成」** 且有条数、耗时 → 分析正常
- **「下游投递成功」** → 投递正常
- 任一 **ERROR**：在该环节排查（网络、API Key、URL、下游接口）。可关注日志：`pipeline run started`、`待重试 batch 数`、`请求 MCP get_latest_news`、`分析模式`、`即将投递下游` 等。

---

## 配置说明

编辑 `config/config.yaml`：

| 配置项 | 说明 |
|--------|------|
| `mcp.base_url` | TrendRadar MCP 的 base URL |
| `mcp.tools.get_latest_news.limit` | 每次拉取条数 |
| `ai.api_base` | 百炼兼容接口，默认 `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `ai.model` | 模型名，如 `qwen-plus`、`qwen3.5-plus` |
| `ai.api_key` | 填 `env:BAILIAN_API_KEY` 表示从环境变量读取（**勿写真实密钥**） |
| `ai.batch_size` | 批量分析每批条数，0 表示单条模式 |
| `downstream.url` | xiaohongshu-mcp 接收 URL |
| `scheduler.interval_seconds` | 定时间隔（秒）；或使用 `scheduler.cron` 的 cron 表达式 |

**敏感信息**：不要将 API Key 或 token 写进 `config.yaml` 或提交到仓库，一律使用环境变量并在配置中写 `env:VAR_NAME`。可选参考 [config/config.example.yaml](config/config.example.yaml) 复制为 `config.yaml` 后按需修改。

---

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `BAILIAN_API_KEY` | 是 | 百炼 API Key |
| `MCP_BASE_URL` | 否 | 覆盖 config 中的 MCP 地址 |
| `XIAOHONGSHU_MCP_URL` | 否 | 覆盖 config 中的下游投递地址 |

本地开发时可在 `.env` 中设置（`.env` 已在 `.gitignore` 中，不会提交）。

---

## 与 xiaohongshu-mcp 的对接约定

- **方法**：`POST`
- **Content-Type**：`application/json`
- **请求体示例**：

```json
{
  "items": [
    {
      "key_points": ["要点1", "要点2"],
      "sentiment": "neutral",
      "topic": "科技",
      "summary": "一段话摘要",
      "source": {
        "title": "新闻标题",
        "url": "https://...",
        "platform": "weibo",
        "rank": 1
      }
    }
  ],
  "batch_id": "uuid",
  "ts": "2025-02-28T12:00:00Z"
}
```

若 xiaohongshu-mcp 尚未实现该接口，可据此实现接收端；本服务会按上述 schema 投递。

---

## Docker 部署

**密钥与 URL 不写死在镜像或 compose 中**，请通过 `.env` 或 `-e` 传入。

1. 在项目根目录创建 `.env`（可参考 `.env.example`），至少设置：

   ```bash
   BAILIAN_API_KEY=your_key
   # 可选：MCP_BASE_URL=...  XIAOHONGSHU_MCP_URL=...
   ```

2. 构建并启动：

   ```bash
   docker compose up -d
   ```

与 TrendRadar、xiaohongshu-mcp 同网段时，可在 `.env` 中设置：

- `MCP_BASE_URL=http://trendradar-mcp:3333/mcp`
- `XIAOHONGSHU_MCP_URL=http://xiaohongshu-mcp:8080/api/ingest`

日志与重试队列会写入 `logs/` 与 `retry_queue/`，compose 已挂载到宿主机，便于排查与持久化。

---

## 错误与重试

- **MCP 不可用**：自动重试 3 次，仍失败则本次任务终止并打 ERROR。
- **百炼 API 失败**：按条重试；单条失败时该条以空分析结果占位，不影响整批投递。
- **下游投递失败**：响应非 2xx 时，将当批 payload 写入 `retry_queue/`，下次调度时会先重试队列再拉新数据。

---

## 安全提示

- **不要**将 `.env` 或任何包含真实 API Key / token 的文件提交到仓库。
- **不要**在 `config/config.yaml` 或 `docker-compose.yml` 中写死密钥；仅使用环境变量（如 `env:BAILIAN_API_KEY`）或通过 `.env` / `-e` 传入。
- 若曾误提交过密钥，请**立即轮换**该密钥，并考虑从 Git 历史中清除敏感内容（如使用 `git filter-repo` 或 BFG）。

---

## 项目结构

```
trendradar-ai-pipeline/
├── config/
│   ├── config.yaml          # 主配置（敏感项用 env: 引用）
│   └── config.example.yaml  # 配置示例，可复制为 config.yaml
├── src/pipeline/
│   ├── main.py               # 入口
│   ├── scheduler.py          # 定时与管道调度
│   ├── ai_analyzer.py        # 百炼 AI 分析
│   ├── downstream.py         # 下游 HTTP 投递
│   ├── config_loader.py      # 配置加载与 env 解析
│   └── models.py             # 数据模型
├── .env.example              # 环境变量模板（复制为 .env 使用）
├── docker-compose.yml
├── pyproject.toml
├── README.md
└── LICENSE
```

---

## 贡献与许可证

欢迎通过 Issue 或 Pull Request 反馈问题与改进。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。安全相关问题请参考 [SECURITY.md](SECURITY.md)。许可证见 [LICENSE](LICENSE)（MIT）。
