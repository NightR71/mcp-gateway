---
id: project-mcp-gateway
type: project
title: "MCP Gateway — 智能体工具网关（王牌项目）"
tags: [MCP, 网关, Gateway, 语义路由, 工具路由, 2-gram, 断线自愈, 重连, 限流, 令牌桶, API Key, 鉴权, 白名单, 多租户, SSE, 流式, Serverless, Vercel, inprocess, NL2SQL, Agent, AgentRunner, FastAPI, asyncio, Prometheus, structlog, Docker, CI, 开源, 测试]
related: [evidence-main-story, evidence-jd-mapping, qa-mcp-llm, qa-python-web, qa-storage-infra, profile-basic]
updated: 2026-09-14
---

# MCP Gateway — 智能体工具网关

## 一句话定位

统一接入并管理多个 MCP Server 的智能体网关：上层 LLM Agent / 业务系统只对接网关一个入口，即可调用背后任意多个 MCP Server 的工具，鉴权、限流、语义工具路由、流式输出、可观测与断线自愈全程覆盖。

## 痛点

1. 工具越来越多，全量塞进 Agent 上下文 → token 膨胀、工具选择质量下降；
2. 多个调用方共享工具 → 无隔离、无配额；
3. 下游 MCP Server 宕机 → 整个入口不可用；
4. AI 调用过程是黑盒，用户看不见。

## 方案（技术要点，全部为本仓库可验证事实）

- **四种传输接入**：stdio（本地子进程）/ SSE / Streamable HTTP / inprocess（进程内加载，Serverless 友好）；新 Server 只需在 `config/gateway.yaml` 声明接入，无需改代码。
- **工具注册中心**：`{server}__{tool}` 命名空间全局唯一（兼容 OpenAI function calling 命名规则）、并发连接、单点失败不阻塞。
- **语义工具路由**：中文 2-gram 切分 + 英文/数字切分，name×3 / description×2 / 参数×1 加权打分，按查询只注入 top-k 相关工具；Scorer 为 Protocol 抽象，可平滑升级 embedding。
- **网关内 Agent**：`POST /agent/run` 完整 Agent 循环（工具路由 → 模型决策 → 网关执行 → 结果回填 → 最终回答）；`Model` 为 Protocol，OpenAI 兼容端点与离线 Mock 可插拔。
- **SSE 流式**：`/agent/run/stream`（step → token* → done）与 `/tools/{name}/call/stream`（start → result → done）。
- **NL2SQL 双引擎**：13 条规则模板兜底 + 可选 LLM 增强（rule / llm / hybrid 三模式，LLM 不可用自动降级，结果标注引擎来源）。
- **安全与多租户**：API Key 鉴权（SQLite 存储，`APIKeyStore` Protocol 可换 Redis/PostgreSQL）、令牌桶限流（429 + Retry-After；另支持小时桶，如访客 50 次/小时）、tenant + 工具白名单（403/404 语义区分）、白名单收口到调用点、入参设界、内部错误不透明化（不透明码 + 关联 ID）。
- **可靠性**：失败 Server 指数退避自动重连（2s→60s）、`asyncio.timeout` 调用总超时（502 明确提示）、`classify_error()` 按 MCP SDK 错误码分类（-32000 断连 / -32001 超时）。
- **可观测**：structlog 结构化 JSON 日志 + Prometheus 指标（工具调用次数/耗时，按 tool/server/status 维度）。
- **交互工作台 /ui**：纯静态三件套（零构建链、零 CDN），调用链时间线 + 富渲染 + SVG 流程动画。

## 个人贡献

独立从零到一完成：接口、模块、测试、CI、Docker 与 Vercel 两套部署形态全部自己落地。开发方式是 AI 协作下的工程闭环：动手前先写约定文档（分层规则、公开契约只增不改的兼容红线），AI 每轮改动必须过全量测试与 lint，失败信息喂回修正，踩过坑固化成回归测试。

## 结果

- **371 项测试通过、1 项按环境跳过**（2026-09-18 `uv run pytest` 实测，随跑随更）；含 stdio/SSE/HTTP 真实子进程集成测试、inprocess 加载生产配置回归、kill 下游 Server 的故障注入自愈测试、安全加固用例（白名单击穿拦截 / 入参设界 / 错误脱敏 / 小时桶）。
- GitHub Actions CI（push/PR 自动 `uv sync --locked` + Ruff + pytest）；ruff check 与 format 全过。
- Docker 双容器部署 + Vercel Serverless 部署，线上可体验：https://mcpgatewaydemo1.vercel.app/ui
- MIT 开源（GitHub 仓库地址 `GITHUB_REPO_URL` 占位保留，T6 已定：暂不展示；被问仓库链接 → 「开源仓库详情建议直接与本人确认，线上演示入口在页面即可体验」）。

## 追问预案

### L1（确认层）
- **为什么做这个网关**：答三痛点（token 膨胀 / 调用方隔离 / 下游宕机），再落到"业务应用与基础设施互相证明"。
- **怎么接入一个新 MCP Server**：YAML 声明（传输类型 + 启动命令/URL），注册中心自动挂 `{server}__{tool}` 命名空间，不改代码。
- **测试怎么写的**：分层——单元 + 真实子进程集成（三种传输真拉起）+ 故障注入（kill 下游看重连）+ 安全回归；CI 兜底。
- **AI 含量**：模型可插拔，配真实 Key 就是真实推理；网关本身就是 Agent 应用的地基，调用工具的 Agent 和被调用的地基我两边都做过。

### L2（深挖层）
- **语义路由为什么 2-gram 而不是 jieba/embedding**：零依赖、零网络、无冷启动开销（Serverless 友好）；2-gram 对中文召回足够，配 top-k 控制 token 预算；Scorer 做成接口，以后换 embedding 业务零改动。
- **inprocess 传输为什么存在**：Vercel Serverless 拉不起 MCP 子进程——子进程解释器看不到构建期依赖，工具注册数为 0；进程内直接加载 `MCPServer` 实例解决，本地 stdio / Docker HTTP 不受影响。部署环境倒逼传输层抽象。
- **断线自愈为什么不按异常类型判断**：MCP SDK 2.x 把对端断连包装成 `MCPError(-32000)`，不是 Python `ConnectionError`——传统写法漏判、自愈不触发；改为 `classify_error()` 按错误码分类（-32000 断连 / -32001 超时）。
- **限流设计**：令牌桶按 Key 维度，429 带 Retry-After；访客场景扩了小时桶（容量 50）；单机内存版，分布式换 Redis+Lua 原子操作（主动承认边界 + 给出升级路径）。
- **白名单为什么 403/404 区分**：403 = 工具存在但该租户无权，404 = 工具不存在——语义清晰且不向访客泄露全集。
- **SSE 在中间件上的坑**：ASGI 中间件伪造 `http.disconnect` 会把 StreamingResponse 的并发断连监听误触发，SSE 被腰斩只剩 2 个事件；httpx 测试客户端恰好掩盖（其 receive 语义与真实服务器不同）。修法是请求体交付后委托真实 receive，并补确定性回归测试。
- **错误脱敏为什么下沉到 core/**：只改路由层留了后门——Agent 循环内部捕获的工具异常会随 steps/SSE 直达公网访客；`log_and_hide` 收口到唯一位置，一处漏掉等于没做。

### L3（挑战层）
- **「这项目像模板/CRUD」**：不辩解，给证据链——仓库开源可查，测试里有真实子进程集成与停服自愈的故障注入；线上 Demo 可现场走全链路；再讲三个只有踩过坑才知道的细节（Vercel 子进程依赖、SDK 错误码分类、SSE 中间件腰斩）。
- **为什么不用现成网关（LiteLLM/One-API 等）**：MCP 工具层的语义路由、`{server}__{tool}` 命名空间、inprocess 进程内加载是 MCP 生态特有问题，通用 API 网关不管理"工具"这层；自研才能把语义路由和租户白名单做进工具维度。
- **embedding 升级路径**：Scorer 已是 Protocol，新增实现即可，路由行为与调用方契约不变——这正是抽象的面试考点。
- **多租户隔离的深度**：Key → tenant → 工具白名单三层；存储层 `APIKeyStore` Protocol 可换 Redis/PG；承认当前是单机内存限流，分布式方案已明确（Redis+Lua）。

## 边界与不作主张（红线，人设必须遵守）

- 开发时间只说 **2026.08 开源上线**，不提前暴露开发窗口（Git 时间戳公开）。
- 测试数用实测口径并随跑随更，不引用旧简历的 156。
- 「线上实测不被 Serverless 缓冲」一条：仓库内有对应实现与测试，但**线上实测记录待复核**（签字项），AI 回答谨慎表述为「SSE 事件契约设计上逐事件下发」。
- 不宣称分布式限流/高并发已实现——单机内存版 + 明确升级路径。

## 可验证证据

- 本仓库 README「设计决策与踩坑（ADR）」六条（含 M1 新增两条：SSE 中间件、脱敏收口）。
- `uv run pytest` → 371 passed / 1 skipped（2026-09-18 实测）；`.github/workflows/ci.yml`；`vercel.json`、`docker-compose.yml`、`config/` 三套平台配置。
- 线上体验：https://mcpgatewaydemo1.vercel.app/ui （演示 Key 见 README，公开信息）。
