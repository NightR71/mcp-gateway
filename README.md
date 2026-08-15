# MCP Gateway — MCP 智能体网关

统一接入并管理多个 MCP Server 的企业级工具网关：上层 LLM Agent 只对接网关一个入口，即可调用背后任意多个 MCP Server 提供的工具，全程覆盖**鉴权、限流、日志、指标**。

## 架构

```
                        ┌─────────────────────────────┐
                        │        LLM Agent / 应用      │
                        │  (LangChain / OpenAI 等)     │
                        └──────────────┬──────────────┘
                                       │ 统一 REST API
                        ┌──────────────▼──────────────┐
                        │       MCP Gateway (FastAPI)  │
                        │  ┌────────────────────────┐  │
                        │  │  鉴权 (API Key)         │  │
                        │  │  限流 (令牌桶)          │  │
                        │  │  日志 / 指标 (横切层)   │  │
                        │  └───────────┬────────────┘  │
                        │  ┌───────────▼────────────┐  │
                        │  │  工具注册中心 registry  │  │
                        │  │  (聚合所有 server 工具) │  │
                        │  └───────────┬────────────┘  │
                        │  ┌───────────▼────────────┐  │
                        │  │  MCP 客户端 (多传输)    │  │
                        │  └────────────────────────┘  │
                        └───────┬───────────┬───────────┘
                        stdio ──┤           ├── Streamable HTTP / SSE
                 ┌──────────────▼──┐   ┌────▼───────────────┐
                 │ MCP Server #1   │   │ MCP Server #2 ...   │
                 │ (demo_sql_server)│   │  (数据库/内部API等) │
                 └─────────────────┘   └─────────────────────┘
```

## 技术栈

Python 3.12 · FastAPI · MCP 官方 SDK（stdio / SSE / Streamable HTTP）· pydantic-settings + YAML · structlog · Prometheus · SQLite（接口层抽象，可换 PostgreSQL）· pytest · Docker · GitHub Actions · uv

## 快速开始

```bash
uv sync                                   # 安装依赖（自动准备 Python 3.12）
uv run uvicorn app.main:app --reload      # 启动开发服务器

uv run pytest                             # 运行测试
uv run ruff check .                       # lint

docker compose up --build                 # 一键启动
```

启动后访问：

- `GET /health` — 健康检查
- `GET /metrics` — Prometheus 指标
- `GET /docs` — OpenAPI 交互文档

## 配置

`config/gateway.yaml`（优先级：代码默认值 < YAML < 环境变量 `GATEWAY_*`）：

```yaml
gateway:
  port: 8000
  log_level: INFO
servers: []   # MCP Server 声明式接入（阶段 2），无需改代码
```

## 项目结构

```
app/
├── main.py          # FastAPI 入口
├── config.py        # 配置中心（pydantic-settings + YAML）
├── core/            # 横切层：security / rate_limit / logging / metrics
├── mcp/             # 协议层：registry / client / transports / schemas
├── api/             # 接口层：deps.py + routes/
└── schemas/         # Pydantic 模型
servers/demo_sql_server/  # 示例 MCP Server（自然语言→SQL，阶段 2/4）
examples/                 # LLM Agent 调用示例（阶段 5）
tests/                    # 单元测试
```

## 开发路线图

- [x] 阶段 1：工程骨架 + CI（/health、/metrics、配置中心、结构化日志）
- [ ] 阶段 2：协议层打通（stdio/SSE/HTTP 三传输客户端 + 工具注册中心 + demo server）
- [ ] 阶段 3：统一 API（GET /tools、POST /tools/{name}/call）+ API Key 鉴权 + 令牌桶限流
- [ ] 阶段 4：可观测完善 + demo_sql_server（NL2SQL）+ 部署上线
- [ ] 阶段 5：Agent 调用示例 + 开源推广

## 企业级拓展路径

多租户 + RBAC · 模型路由（类比 One-API）· 审计合规 · K8s 自动扩缩容 · OpenTelemetry 链路追踪 · 熔断降级 / 缓存
