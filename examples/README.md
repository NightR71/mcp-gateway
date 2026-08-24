# examples/ — LLM Agent 调用示例（阶段 5）

> 演示「LLM Agent → MCP Gateway → MCP Server」完整闭环：Agent 不直连任何 MCP Server，
> 只通过网关的统一 REST API（`GET /tools`、`POST /tools/{name}/call`）调用聚合工具，
> 鉴权、限流、日志、指标全部由网关一层覆盖。

## 文件

| 文件 | 说明 | 依赖 |
|---|---|---|
| `gateway_client.py` | 网关 REST 极简异步客户端 + OpenAI function schema 转换 | httpx |
| `openai_agent.py` | OpenAI function-calling Agent（纯 httpx 手写协议，不依赖 openai SDK，支持 `--mock` 离线演示） | httpx |
| `langchain_agent.py` | LangChain Agent（网关工具包装成 LangChain Tools + `bind_tools`） | `uv sync --group agent` |

## 前置准备

```bash
uv sync                        # 安装项目依赖
uv run uvicorn app.main:app    # 终端 1：启动网关（启动时自动连上 demo_sql_server）
```

## 0. 浏览器工作台（不写代码也能看完整链路）

启动网关后浏览器打开 `http://localhost:8000/`（或 `/ui`）——**MCP Agent Workbench**：
输入问题（如「查询目前销售额最高的商品」），时间线逐卡展示 工具选择（语义路由注入
top-k）→ 网关鉴权/限流/执行 → 工具结果 → 最终回答，并显示工具总数/注入数/轮数与延迟。
默认 `agent.mock: true` 开箱即用（不需要任何 LLM Key）；顶部 Key 输入框填演示 Key
`dev-key-please-change` 即可。

## 1. 离线演示（推荐先跑，不需要任何 API Key）

```bash
uv run python examples/openai_agent.py "有多少客户？" --mock
```

`--mock` 用内置假模型模拟 LLM 的 function-calling 决策，真实走
「网关鉴权 → 令牌桶限流 → 路由到 demo_sql_server 执行 SQL」全链路：

```
网关：http://localhost:8000（已聚合 4 个工具）
提问：有多少客户？
模型：离线假模型（--mock）
Agent 开始往返（LLM <-> 网关）...

===== 最终回答 =====

（离线演示·假模型）网关返回的工具结果：
生成的 SQL：
```sql
SELECT COUNT(*) AS customer_count FROM customers
```

查询结果：
| customer_count |
| --- |
| 5 |
```

## 2. 真实模型（OpenAI function calling）

```powershell
$env:OPENAI_API_KEY = "sk-..."   # PowerShell；bash 用 export OPENAI_API_KEY=sk-...
uv run python examples/openai_agent.py "总销售额是多少？"
```

模型先决定调用 `demo_sql__ask`，网关执行 SQL 后把结果回填，模型再生成最终中文回答。
`OPENAI_BASE_URL` 可指向任意 OpenAI 兼容端点（One-API / Ollama / DeepSeek 等），
`OPENAI_MODEL` 指定模型名——换模型零代码改动。

## 2.1 连公网部署（Vercel Demo，不用启动本地网关）

示例是纯 HTTP 客户端，指向哪台网关由 `GATEWAY_BASE_URL` 决定：

```powershell
$env:GATEWAY_BASE_URL = "https://mcpgatewaydemo1.vercel.app"
uv run python examples/openai_agent.py "有多少客户？" --mock
# 或一次性：uv run python examples/openai_agent.py "有多少客户？" --mock --base-url https://...
```

Key（`dev-key-please-change`）与限流（60 次/分钟）和本地一致；首次请求是 Serverless
冷启动，等 1–3 秒属正常。⚠️ `*.vercel.app` 在大陆被 DNS 污染（本机直连超时），
海外网络/科学上网可用；国内观众建议绑自定义域名或换国内云服务器（`docker compose` 已验证），
远程验收可用仓库的 `demo-health` GitHub Actions 工作流（海外 Runner 跑 4 步检查）。

## 3. LangChain 版

```bash
uv run --group agent python examples/langchain_agent.py "有多少客户？"
```

`--group agent` 让 uv 临时把可选依赖组（langchain-core + langchain-openai）纳入运行环境
（首次自动安装，网关本体依赖不变）。注意：**不要**用「先 `uv sync --group agent`
再 `uv run python ...`」两步——不带 `--group` 的 `uv run` 会自动把环境重新同步回
默认组并裁掉 langchain，导致第二步报缺依赖。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `GATEWAY_BASE_URL` | `http://localhost:8000` | 网关地址 |
| `GATEWAY_API_KEY` | `dev-key-please-change` | 演示 Key（`config/gateway.yaml` 的 auth 节） |
| `OPENAI_API_KEY` | 无 | LLM API Key（`--mock` 不需要） |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容端点 |
| `OPENAI_MODEL` | `gpt-4o-mini` | 模型名 |

## 设计要点

- **工具名命名空间**：网关工具名统一 `{server}__{tool}`（如 `demo_sql__ask`），天然满足
  OpenAI / LangChain 工具命名规则 `^[a-zA-Z0-9_-]+$`，Agent 侧无需二次改名
  （见 `app/mcp/schemas.py` 中 `NAMESPACE_SEPARATOR` 的设计说明）。
- **零网关内部依赖**：示例只依赖两个 REST 契约，任何语言 / 框架的 Agent
  都能按同样方式接入网关。
- **鉴权 / 限流透明**：所有调用带 `X-API-Key`；超限时网关返回 429（带 Retry-After），
  重试策略由调用方决定。
- **测试**：`tests/test_examples/` 用假模型 + ASGI 传输离线跑通全链路：

  ```bash
  uv run pytest tests/test_examples
  ```
