# MCP Gateway — 智能体工具网关

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-asyncio-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![MCP](https://img.shields.io/badge/MCP-官方SDK-8A2BE2)](https://modelcontextprotocol.io/)
[![Tests](https://img.shields.io/badge/tests-386%20passed-3FB950)](.github/workflows/ci.yml)
[![CI](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF?logo=githubactions&logoColor=white)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

**统一接入并管理多个 MCP Server 的智能体网关 + 建在它之上的业务应用**：上层 LLM Agent / 业务系统只对接网关一个入口，即可调用背后任意多个 MCP Server 的工具；鉴权、限流、语义工具路由、流式输出、可观测与断线自愈全程覆盖。仓库里还有**第二层——业务应用**：一个打开即可对话的 AI 简历助手（`/chat`），它的每一次回答都真实走过这套网关（访客鉴权 → 限流 → 知识库语义检索 → 模型生成 → SSE 逐字回填），基础设施与业务应用互相证明。

## 在线体验

**https://mcpgatewaydemo1.vercel.app/chat** —— 交互式 AI 简历助手（本仓库的业务应用，推荐从这里开始）

- 打开即用、无需填任何 Key：点右侧项目气泡卡（如「这个网关最难的三个坑是什么？」）或直接提问，观察 **访客鉴权 → 令牌桶限流 → 语义检索知识卡片 → DeepSeek 生成 → SSE 逐字回填** 的完整链路，右栏流程图的节点由真实 `step` 事件逐步点亮。
- `/resume` 为同源挂载的脱敏简历页，界面常驻入口。
- Serverless 部署（MCP Server 进程内加载，冷启动 1–3 秒属正常）；`*.vercel.app` 在中国大陆受 DNS 污染可能无法直连，海外网络或 GitHub Actions（`demo-health` workflow）可直接验收。

> **网关侧工作台 `/ui` 仍可访问**（纯静态页面，页面与功能未做删改），但**不再对外发布演示 Key**：公开仓库与页面里不再提供可直接使用的网关 Key，所以它在公网状态下只能看界面、跑不出调用结果。
> 想完整走一遍基础设施侧演示（Agent 工具选择 → 网关鉴权限流 → MCP 调用 → 结果回填 → 流式回答），请在本地按「快速开始」起服务——`config/gateway.yaml` 是 mock 离线配置，零 token、零外部依赖。

![Agent Workbench 运行效果](assets/workbench.png)

## 它解决什么问题

| 场景痛点 | 网关的答案 |
|---|---|
| 工具越来越多，全量塞进 Agent 上下文 → token 膨胀、选择质量下降 | 语义工具路由：按查询只注入 top-k 相关工具 |
| 多个调用方共享工具 → 无隔离、无配额 | API Key 鉴权 + 令牌桶限流 + 租户/工具白名单 |
| 下游 MCP Server 宕机 → 整个入口不可用 | 指数退避自动重连，恢复后工具自动重新注册 |
| AI 调用过程是黑盒，用户看不见 | 步骤 Trace + SSE 流式 + 可视化工作台 |

## 核心特性

### 业务应用层

- **交互式 AI 简历助手 `/chat`（M4）**：纯静态移动端优先三区布局（主体对话流 / 右栏项目气泡卡 + 简历卡 / 对话态运行流程图），SSE 逐字渲染；运行流程由**真实 `step` 事件驱动节点逐个点亮**（流式异常才降级为预定义示意）；窄屏下气泡横向滚动、对话与流程可切换；无模型或未放行演示时显示维护页，不用假剧本冒充回答
- **简历知识库只读服务（M3）**：`servers/resume_kb_server/` 只读工具集（`search_knowledge` / `get_card` / `list_cards` / `get_profile`），检索 `knowledge/` 下的 Markdown 知识卡片（YAML frontmatter + 中文 2-gram 检索）；访客 Key 白名单通配 `resume_kb__*`
- **回答可控的三道闸**：人设系统提示词（客观克制 + 拒答红线 + 防注入约束）、输入侧固定回应（命中红线词即直接返回话术、**不进模型**）、输出守门（流式滑动窗口扫描手机号 / 邮箱等 PII 并即时打码）——全部配置化于 `config/resume_kb.yaml`，改文案不动代码
- **线上护栏**：访客 Key 只能调知识库工具且限 50 次/小时；单次问答的 token 有界——`max_rounds` 限轮数、`max_tokens` 限单次输出（服务端在请求体里强制，流式与非流式两条路径都带）；另有 `agent_allowed: false` 的只读验证 Key（在线证明"公开 Key 即便泄露也烧不到模型余额"）；管理员凭 `GATEWAY_ADMIN_KEY` 调 `/admin/mode` 在 public / internal / closed 三态间切换（应急成本闸门）；GitHub Actions 每 6 小时拨测一轮（10 步编号断言 + 诊断 + 失败自动开 issue，含真实模型连通性与回答质量断言）
- **脱敏 `/resume` 页**：零联系方式、零成绩主张、雇主一律行业描述

### 基础设施层

- **四种传输接入**：stdio（本地子进程）/ SSE / Streamable HTTP / inprocess（进程内加载，Serverless 友好）
- **工具注册中心**：`{server}__{tool}` 命名空间全局唯一（兼容 OpenAI function calling 命名规则）、并发连接、单点失败不阻塞
- **语义工具路由**：中文 2-gram 切分 + 名称/描述/参数加权打分，`GET /tools?query=销售额&top_k=2`；Scorer 为 Protocol 抽象，可平滑升级 embedding
- **网关内 Agent**：`POST /agent/run` 完整 Agent 循环（工具路由 → 模型决策 → 网关执行 → 结果回填 → 最终回答），`Model` Protocol 可插拔（OpenAI 兼容端点 / MockModel 离线演示）
- **SSE 流式**：`POST /agent/run/stream`（`step → token* → done`）与 `POST /tools/{name}/call/stream`（`start → result → done`）；部署到 Serverless 后仍逐事件下发（由定时拨测验证，见「在线体验」）
- **NL2SQL 双引擎**：13 条规则模板兜底 + 可选 LLM 增强（`rule / llm / hybrid` 三模式，LLM 不可用自动降级，结果标注引擎来源）
- **安全与多租户**：API Key 鉴权（SQLite 存储，`APIKeyStore` Protocol 可换 Redis/PostgreSQL）、令牌桶限流（429 + `Retry-After`）、tenant + 工具白名单（403/404 语义区分）
- **可靠性**：失败 Server 指数退避自动重连（2s→60s）、`asyncio.timeout` 调用总超时（502 明确提示）、`classify_error` 按 MCP SDK 错误码分类（识别 `-32000` 断连 / `-32001` 超时）
- **可观测**：structlog 结构化 JSON 日志 + Prometheus 指标（工具调用次数/耗时，按 tool/server/status 维度）
- **交互工作台 `/ui`**：纯静态三件套（零构建链、零 CDN），调用链时间线 + SQL 高亮/表格富渲染 + 运行流程 SVG 动画
## 快速开始

```bash
uv sync                                   # 安装依赖（自动准备 Python 3.12）
uv run uvicorn app.main:app --reload      # 启动开发服务器（默认 stdio 拉起 demo server）
uv run pytest                             # 运行测试
uv run ruff check .                       # lint

docker compose up --build                 # Docker 双容器部署（gateway + demo_sql）
```

常用接口：

```bash
KEY="dev-key-please-change"   # 本地演示 Key（见 config/gateway.yaml；线上不提供公开演示 Key）

curl http://localhost:8000/health
curl -H "X-API-Key: $KEY" "http://localhost:8000/tools?query=销售额&top_k=2"

curl -X POST http://localhost:8000/agent/run -H "X-API-Key: $KEY" \
     -H "Content-Type: application/json" -d '{"question": "有多少客户？"}'

curl -N -X POST http://localhost:8000/tools/demo_sql__ask/call/stream \
     -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
     -d '{"arguments": {"question": "有多少客户？"}}'    # SSE: start → result → done
```

`demo_sql_server` 内置迷你电商库（customers / products / orders），提供 4 个工具：
`ask`（中文提问 → 自动生成并执行只读 SQL）、`run_sql`（直接执行只读 SQL）、
`list_tables`（表结构）、`echo`（链路调试）。只读校验（仅 SELECT/WITH、危险关键字拦截、
50 行截断）在工具层强制执行。接入新的 MCP Server 只需在 `config/gateway.yaml` 声明，无需改代码。

## Agent 调用示例（examples/）

演示「LLM Agent → 网关 → MCP Server」完整闭环，Agent 不直连任何 MCP Server，
只通过网关统一 REST API 调用聚合工具：

```bash
uv run uvicorn app.main:app                                    # 先启动网关
uv run python examples/openai_agent.py "有多少客户？" --mock    # 离线演示（假模型，无需 API Key）
uv run python examples/openai_agent.py "总销售额是多少？"        # 真实 OpenAI function calling
uv run --group agent python examples/langchain_agent.py "有多少客户？"  # LangChain 版
```

两个版本：`openai_agent.py`（纯 httpx 手写 function-calling 协议，`OPENAI_BASE_URL`
可指向 One-API / Ollama / DeepSeek 等兼容端点）、`langchain_agent.py`（网关工具
动态包装成 LangChain Tools）。详见 `examples/README.md`。

## 架构

```
面试官浏览器 / 调用方
  ├── /chat    AI 简历助手（业务应用）：对话流 + 项目气泡卡 + 真实 step 点亮流程图
  ├── /resume  脱敏简历页：零联系方式 / 零成绩主张 / 雇主行业描述
  └── /ui      工作台（基础设施侧演示）：调用链时间线 + SQL 富渲染
        │
        │  REST + SSE（/agent/run/stream：step → token* → done）
        ▼
  Gateway (FastAPI)
    鉴权 401 → 限流 429 → 白名单 403 → 语义路由 → Agent 循环 → 工具执行
    app/agent/  AgentRunner + Model（OpenAI 兼容端点 / Mock）
    app/mcp/    ToolRouter + Registry（{server}__{tool} 命名空间）
    四传输 client（stdio / SSE / Streamable HTTP / inprocess）+ 断线自愈 + 总超时
    structlog 结构化日志 + Prometheus 指标
        │
        ├── stdio / inprocess ──► resume_kb（知识库只读检索）/ demo_sql_server（NL2SQL 双引擎）
        └── http / sse ────────► 其他 MCP Server（任意，YAML 声明式接入）
```

## 设计决策与踩坑（ADR）

1. **Vercel Serverless 拉不起 MCP 子进程**（子进程解释器看不到构建期依赖，工具注册数为 0）→ 新增 `inprocess` 传输，在网关进程内直接加载 `MCPServer` 实例；本地 stdio / Docker HTTP 不受影响。*部署环境倒逼传输层抽象。*
2. **MCP SDK 2.0.0 的 `streamable_http_client` 只 yield 2 元组**（旧教程为 3 元组），该缺陷潜伏到 Docker 冒烟才暴露 → 以实装 SDK 为准适配，并补独立 http 子进程集成测试。*集成测试兜住 SDK 版本差异。*
3. **SDK 2.x 把对端断连包装成 `MCPError(-32000)`**，不是 Python `ConnectionError`——按传统异常类型判断会漏判、自愈不触发 → `classify_error()` 按 SDK 错误码分类（`-32000` 断连 / `-32001` 读超时）。*自愈闭环的最后一公里。*
4. **`*.vercel.app` 大陆 DNS 污染**无法自验线上 → 用 GitHub Actions `demo-health` workflow 从海外 Runner 跑线上拨测（现已扩到 10 步编号断言：鉴权 / 白名单 / 能力位 / 零 Key 外泄 / 三页可达 / 真模型连通性与输出守门 / 线上 5 问复验）。*工程闭环不依赖本地网络。*
5. **ASGI 中间件伪造 `http.disconnect` 会腰斩 SSE**：请求体限流中间件初版在请求体交付后直接返回断连，被 Starlette `StreamingResponse` 的并发断连监听当作客户端断开并取消整个响应流（uvicorn 实测 SSE 只剩 2 个事件、`done` 丢失，日志报 `ASGI callable returned without completing response`）。httpx 测试恰好掩盖了它——其 `receive()` 会等响应完成后才返回断连 → 请求体交付后一律**委托真实 `receive`**，并补「读完体后挂起」的确定性回归测试。*测试客户端与真实服务器的语义差异会掩盖缺陷，流式改动必须真机复验。*
6. **脱敏只做在路由层 = 留后门**：安全加固把 4 处路由 5xx 改成不透明文案后，Agent 循环内部仍有一条出口——工具调用失败被循环自己捕获，`f"工具调用失败：{exc}"` 随响应 `steps` 与 SSE 逐条事件直达公网访客（还回填模型上下文）。修法是把 `log_and_hide` 下沉到 `core/` 供接口层与 Agent 层共用，循环内只对**预期内工具错误**保留可读文案。*与白名单同样的道理：横切能力必须收口到唯一位置，一处漏掉等于没做。*
7. **公开演示 Key 必须"点不动模型"，而不只是"限流小一点"**：`/chat` 要能匿名体验就得公开一个 Key，于是把 `agent_allowed: false` 做成一等能力位（白名单只到 `resume_kb__*`，调 `/agent/run` 直接 403），并额外发一个只读验证 Key，由拨测每次在线断言这条边界。*对外暴露面，靠"少给点"不如靠"给不了"。*
8. **三态开关的确定性边界：Vercel 上为什么是只读**：`public_mode`（public / internal / closed）在单进程部署下可经 `/admin/mode` 即时切换（`public_mode_store: process`）；Serverless 是多实例、内存互不可见，于是线上用 `config`（只读）——切换要改配置并重新部署。代价是慢，换来的是**不会出现"一部分实例已关闭、另一部分还在放行"的中间态**。*分布式下的一致性，宁可用部署的慢换确定的快。*
9. **决策轮旁白的结构性抑制（按轮缓冲 + 最终轮回放）**：模型在"决策轮"会输出「I'll search the knowledge base…」这类过程旁白，逐字流式会把它直接漏给面试官。提示词加约束不可靠（换模型就复发），最后改成结构方案：决策轮 token 先缓冲、不进 SSE，只在**最终回答轮**回放并对缓冲内容做旁白过滤。*能用结构解决的，不要指望提示词。*
10. **拨测要能天天跑，就得把最贵那步关进笼子**：`demo-health` 每 6 小时跑一轮（10 步编号断言 + 诊断 + 失败自动开 issue），其中打真实模型的步骤由小时门控限制为 **≤1 次/天**——常态断言零模型调用，真模型只在"每日一次"的窗口里跑连通性 + 输出守门 + 线上 5 问复验。*监控的可持续性由成本决定，不是由覆盖率决定。*

## 配置

`config/gateway.yaml`（优先级：代码默认值 < YAML < 环境变量 `GATEWAY_*`）：

```yaml
gateway:
  port: 8000
  tool_call_timeout: 30.0     # 单次工具调用总时长兜底
  max_request_body_bytes: 1000000   # 请求体上限（超限 413；限流限次数，这里限单次体积）
  public_mode: public         # 三态公开开关：public / internal / closed（默认 closed=fail-closed）
  public_mode_store: process  # 单进程部署可写（/admin/mode 即时切换）；多实例 Serverless 用 config（只读）
auth:                          # API Key 鉴权（SQLite 存储，启动种子写入）
  api_keys:
    - { key: dev-key-please-change, name: demo, tenant: demo, rate_limit_per_minute: 60 }
    - { key: limited-tools-key, name: limited-tools, tenant: demo,
        rate_limit_per_minute: 60, allowed_tools: ["demo_sql__ask"] }   # 白名单演示
    # agent_allowed: false    # 可选：该 Key 只能调 MCP 工具，不能触发 Agent（模型调用）
    # rate_limit_per_hour: 50  # 可选：每小时配额（小时桶，慢速补充）
  # 管理员 Key 的配额（凭据只走环境变量 GATEWAY_ADMIN_KEY，不落 YAML / 不落库）
  admin: { tenant: admin, rate_limit_per_minute: 30, rate_limit_per_hour: 300 }
routing:                       # 语义工具路由
  { enabled: true, top_k: 10, min_tools: 3 }
agent:                         # 网关内 Agent（mock 开箱即用；真实模型配 GATEWAY_AGENT_API_KEY）
  { enabled: true, mock: true, model: gpt-4o-mini, max_rounds: 8 }
metrics:                       # 默认不暴露；开启后默认要求 API Key（本机/内网可设 require_auth: false）
  { enabled: false, require_auth: true }
servers:                       # MCP Server 声明式接入，无需改代码
  - name: demo_sql
    transport: stdio           # stdio / sse / http / inprocess
    command: python
    args: ["servers/demo_sql_server/server.py"]
```

Docker Compose 使用 `config/gateway.docker.yaml`：demo_sql 以独立容器跑
Streamable HTTP，网关经 `http://demo_sql:9001/mcp` 连接。

## 测试与质量

- **386 项测试**（`uv run pytest`，2026-09-20 实测，另有 1 例按环境跳过；随开发增长，知识库与被核对时给精确值 + 日期）：stdio/SSE/HTTP 真实子进程集成测试、inprocess 加载 Vercel 生产配置回归测试、kill 下游 Server 后自动恢复的故障注入测试、安全前置用例（白名单击穿拦截 / 入参设界 413·422 / 错误不透明化含 Agent 步骤出口 / 小时桶 + /metrics 策略）、成本护栏用例（`max_tokens` 流式与非流式两条路径都进请求体 / 默认不下发该字段 / YAML 显式配置才生效）、评测集零成本回归（65 问的召回 / 红线命中 / 防误伤）、简历知识库用例（知识库数据质量含 PII 零入断言 / 语义召回 / 白名单通配 / 人设注入与固定回应 / 输出守门流式打码）
- `ruff check` + `ruff format --check` 全过；GitHub Actions 在 push / PR 时执行 `uv sync --locked` + Ruff + pytest
- 兼容红线：`/tools`、`/tools/{name}/call`、`/servers` 等公开契约只做增量扩展；examples/ 与网关双向无依赖

## 项目结构

```
app/
├── main.py          FastAPI 入口（lifespan：registry / key_store / rate_limiter / agent）
├── config.py        配置中心（默认值 < YAML < 环境变量）
├── core/            security / rate_limit / logging / metrics / body_limit / errors
│                    output_guard（M3 输出守门：流式滑窗 PII 打码）
├── mcp/             transports · client · registry · tool_router · schemas
├── agent/           AgentRunner · Model Protocol（OpenAI 兼容 / Mock）· persona（M3 人设）
├── api/             deps.py 依赖注入 + routes/（tools · servers · agent · health · metrics）
├── schemas/         Pydantic 请求/响应模型（含 /chat 前端契约）
├── ui/              浏览器工作台（纯静态 HTML/CSS/JS，基础设施侧演示入口）
├── chat/            简历助手对话页（M4：三区布局 + 真实 step 点亮流程图 + 维护态）
└── resume/          脱敏简历页（M4：零联系方式 / 零成绩主张 / 雇主行业描述）
servers/demo_sql_server/   示例 MCP Server（4 工具，NL2SQL 双引擎，只读校验）
servers/resume_kb_server/  简历知识库只读 MCP Server（M3：4 检索工具 + 卡片加载/打分）
knowledge/                 知识卡片（Markdown + YAML frontmatter，只读加载）
examples/                  OpenAI function-calling 与 LangChain 调用网关的 Agent 示例
tests/                     与 app 结构对应的单元/集成测试
config/                    各平台配置（本地 stdio / Docker http / Vercel inprocess）+ resume_kb.yaml
```

## Roadmap

- [x] 四传输 MCP 接入 + 工具注册中心 + 统一 REST API + 鉴权限流 + 可观测
- [x] 语义工具路由 + 网关内 Agent + /ui 工作台 + SSE 流式
- [x] NL2SQL 双引擎（规则 + LLM）+ 多租户/工具白名单 + 断线自愈/总超时/错误分类
- [x] 安全加固：白名单收口到调用点 + 入参设界（长度/轮数/体积）+ 内部错误脱敏 + /metrics 默认关闭 + 小时桶限流
- [x] 简历知识库只读服务（M3）：`resume_kb` 工具集 + 知识卡片检索 + 人设系统提示词 + 红线固定回应 + 流式输出守门（PII 打码）+ 访客白名单通配
- [x] 简历助手前端（M4）：`/chat` 三区布局 + 真实 step 点亮流程图 + 维护态（real-model-only）+ `/resume` 脱敏简历页；移动端优先（窄屏气泡横滚与对话/流程切换）
- [x] 线上部署与护栏（M6）：Vercel Serverless 真模型上线 + 公开 Key 能力位收口（点不动模型）+ 三态开关 + `/chat` 作为对外入口 + 每 6 小时拨测（10 步编号断言，真模型探测门控 ≤1 次/天）
- [x] 收尾（M7）：README 两层叙事（基础设施 + 业务应用）+ 简历与知识库数字口径统一 + 演示脚本与面试材料
- [x] 评测集常态化（零成本侧）：65 问面试官高频问题结构化为数据文件进 CI——召回命中 / 红线固定话术命中 / 正常提问不被误拦三类断言，改知识库或人设后自动回归
- [ ] 评测集常态化（真模型侧）：真模型回归脚本（限量手动跑，判定"回答是否覆盖要点、是否零 PII"）
- [ ] 国内部署路径（已核实为配置层工作：新平台 YAML + 入口适配，无需重构）
- [ ] embedding 语义路由（Scorer 已预留接口）
- [ ] Redis 分布式限流 + Key 哈希存储
- [ ] OpenTelemetry 链路追踪 / 压测报告

## 关于第三方名称（声明）

本仓库是**个人求职用的作品集仓库**，用于在面试中展示本人的工程实践。其中的经历描述均为本人真实经历，并已按行业口径脱敏：知识库卡片、`/resume` 页面与线上回答里**不含真实公司名**（这一条是被测试与拨测持续断言的，见 `tests/test_servers/test_resume_kb.py` 与 `.github/workflows/demo-health.yml`）。

测试与拨测代码中保留了少量真实公司名称，用途是**反向校验**——它们是一份「禁止出现」清单，断言这些名称不会出现在知识库、前端产物与线上回答里（没有这份清单，这条红线就无法被自动验证）。此外，项目背景源于本人真实工作经历，相关技术选型与业务场景在描述上难以完全避免指向具体雇主。

如相关公司认为上述提及不妥，请在本仓库提交 issue 与我联系，我会立即删除相关内容。

## License

[MIT](LICENSE)
