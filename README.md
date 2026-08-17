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
- `GET /metrics` — Prometheus 指标（含工具调用次数/耗时：`mcp_gateway_tool_calls_total`、`mcp_gateway_tool_call_duration_seconds`）
- `GET /docs` — OpenAPI 交互文档

调用示例（演示 Key 见 `config/gateway.yaml` 的 auth 节）：

```bash
curl -H "X-API-Key: dev-key-please-change" http://localhost:8000/tools

curl -X POST http://localhost:8000/tools/demo_sql__ask/call \
     -H "X-API-Key: dev-key-please-change" -H "Content-Type: application/json" \
     -d '{"arguments": {"question": "有多少客户？"}}'
```

`demo_sql_server` 内置迷你电商库（customers / products / orders），提供 4 个工具：
`ask`（中文提问 → 自动生成并执行只读 SQL）、`run_sql`（直接执行只读 SQL）、
`list_tables`（表结构）、`echo`（链路调试）。NL2SQL 为规则模板引擎，
离线零依赖，接口与 LLM 实现解耦，可平滑替换。

## 配置

`config/gateway.yaml`（优先级：代码默认值 < YAML < 环境变量 `GATEWAY_*`）：

```yaml
gateway:
  port: 8000
  log_level: INFO
auth:                 # API Key 鉴权（SQLite 存储，启动种子写入）
  db_path: data/gateway.db
  api_keys:
    - { key: dev-key-please-change, name: demo, rate_limit_per_minute: 60 }
servers:              # MCP Server 声明式接入，无需改代码
  - name: demo_sql
    transport: stdio  # stdio / sse / http
    command: python
    args: ["servers/demo_sql_server/server.py"]
```

Docker Compose 使用 `config/gateway.docker.yaml`：demo_sql 以独立容器跑
Streamable HTTP，网关经 `http://demo_sql:9001/mcp` 连接。

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
- [x] 阶段 2：协议层打通（stdio/SSE/HTTP 三传输客户端 + 工具注册中心 + demo server）
- [x] 阶段 3：统一 API（GET /tools、POST /tools/{name}/call）+ API Key 鉴权 + 令牌桶限流
- [x] 阶段 4：工具调用指标 + demo_sql_server 升级 NL2SQL + docker-compose 双容器（公网部署与演示录屏待补）
- [ ] 阶段 5：Agent 调用示例 + 开源推广

## 企业级拓展路径

多租户 + RBAC · 模型路由（类比 One-API）· 审计合规 · K8s 自动扩缩容 · OpenTelemetry 链路追踪 · 熔断降级 / 缓存
