# AGENTS.md — MCP Gateway 项目约定

## 项目概述

统一接入并管理多个 MCP Server 的企业级工具网关：上层 LLM Agent 只对接网关一个入口，即可调用背后任意多个 MCP Server 的工具，全程覆盖鉴权、限流、日志、指标。

完整规划见 `docs/MCP-Gateway项目规划与开发步骤.md`。开发时按该文档的「阶段」推进，每完成一个阶段更新其第 11 节「当前进度」。

## 技术栈

- Python 3.12 + FastAPI + uvicorn + asyncio
- MCP 官方 `mcp` SDK（stdio / SSE / Streamable HTTP 三种传输）
- pydantic-settings + YAML 配置、structlog 结构化日志、prometheus-fastapi-instrumentator
- 鉴权自实现 API Key + python-jose（预留 JWT）；限流自实现令牌桶
- SQLite（MVP，接口层抽象，可换 PostgreSQL）
- pytest + pytest-asyncio + httpx；Docker + docker-compose；GitHub Actions CI
- 包管理：uv（pyproject.toml）

## 常用命令

```bash
uv sync                                   # 安装依赖
uv run uvicorn app.main:app --reload      # 启动开发服务器
uv run pytest                             # 运行测试
uv run ruff check .                       # lint
uv run ruff format .                      # 格式化
docker compose up --build                 # 一键启动（gateway + demo server）
```

## 目录结构与分层（严格遵守）

```
app/
├── main.py          # FastAPI 入口（挂路由、中间件、启动/清理事件）
├── config.py        # pydantic-settings 配置中心，禁止写死常量
├── core/            # 横切关注点：security / rate_limit / logging / metrics
├── mcp/             # 协议层：registry（核心）/ client / transports / schemas
├── api/             # 接口层：deps.py 依赖注入 + routes/
└── schemas/         # Pydantic 模型
servers/demo_sql_server/  # 示例 MCP Server（自然语言→SQL）
examples/                 # LLM Agent 调用示例
tests/                    # 单元测试（与 app 结构一一对应）
```

分层原则：路由层只做参数解析和响应组装，业务逻辑不放路由；协议细节只在 `app/mcp/`；配置只进 `config.py`。

## 编码约定

- 全部 `async def`，IO 一律 asyncio，禁止阻塞事件循环
- 完整类型注解；Pydantic v2 模型定义请求/响应
- MCP Server 一律走 config YAML 声明接入，不写死
- 工具名全局唯一，跨 server 用 namespace 前缀避免冲突
- 每个接口测试覆盖：成功 / 401 未授权 / 429 限流 三个 case
- 提交前必须 `ruff check` 和 `pytest` 通过

## 接入的 MCP / 参考

- 已配 GitHub MCP（需环境变量 `GITHUB_TOKEN`，Personal Access Token）
- 已配 Playwright MCP（需本机 Node.js）
- references：`mcp-sdk`（MCP Python SDK 源码）、`fastapi`（FastAPI 源码）
- 项目专属 skill：`python-backend`、`mcp-dev`
