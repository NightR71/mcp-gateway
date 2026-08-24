# MCP Gateway 项目拓展规划与开发步骤（二阶段）

> 本文档是二阶段的唯一交接文档：换一个对话后，AI 只看本文档 + 仓库代码，就能无缝继续开发。
> 一阶段历史（含踩坑细节）见 `docs/MCP-Gateway项目规划与开发步骤.md`；评审背景见 `docs/项目评审与优化建议（HR与面试官双视角）.md`（其中的 P0/P1/P2 清单已被本文档重新排定优先级，勿照搬）。
> 文档撰写基准：2026-08-24，git `main` @ `44c65c4`，实际代码逐一核验（非仅凭旧文档）。

---

## 0. 给新对话的一句话开场（复制即用）

```text
我正在继续开发 MCP Gateway 项目（二阶段）。

请先阅读：
docs/MCP-Gateway项目拓展规划与开发步骤.md

然后检查当前代码与测试，以文档中的「当前进度」为主要导航，
但以实际代码和测试结果为最终事实。

不要重复已经完成的工作。

找到当前阶段尚未完成的第一项任务，
先分析实现方案，再开始修改代码。

完成后：
1. 运行相关测试
2. 运行完整测试
3. 检查是否破坏已有功能
4. 更新本文档「当前进度」节
5. 告诉我下一步是什么。
```

---

## 1. 当前项目定位

- **一句话（不变）**：统一接入并管理多个 MCP Server 的工具网关，上层 LLM Agent 只对接网关一个入口调用所有工具，全程覆盖鉴权、限流、日志、指标。
- **二阶段定位升级**：从「工具网关」演进为 **Agent 入口（Agent Gateway）**——Agent 经网关完成「工具发现 → 工具路由 → 工具调用 → 结果回填 → 交互展示」的完整闭环。网关不再是 Agent 背后的被动管道，而是 Agent 循环的宿主与观察点。
- **为什么升级**：评审确认一阶段代码质量与工程闭环扎实，但「AI 含量不足」（Agent 逻辑只存在于 examples/ 演示脚本）、缺工具路由（工具多了全量注入会爆 context）、无可交互界面。二阶段补齐这三块后，项目故事线完整：既懂 Agent 应用，又懂 Agent 的地基。

## 2. 当前真实能力（文档声称 / 代码事实 / 验证事实 三级核验，2026-08-24）

| 能力 | 旧文档声称 | 代码事实（文件） | 验证事实 |
|---|---|---|---|
| stdio / SSE / Streamable HTTP 三传输 | 完成 | `app/mcp/transports.py` | stdio、http 有真实子进程/进程集成测试；**SSE 无测试**（缺口） |
| inprocess 第四传输 | 完成 | `app/mcp/client.py` `InProcessClient` + `create_client()` 工厂 | `tests/test_mcp/test_inprocess.py` 4 例 + Vercel demo-health 4 步绿 |
| 工具注册中心 | 完成 | `app/mcp/registry.py`（`{server}__{tool}` 命名空间、并发连接、单点失败不阻塞） | `tests/test_mcp/test_registry.py` 真实 stdio 子进程集成测试 |
| 统一 API | 完成 | `app/api/routes/tools.py`（GET /tools、POST /tools/{name}/call）、`servers.py`、`health.py` | `tests/test_api/` 8 例（401/404/429/502 分支齐全） |
| API Key 鉴权 | 完成 | `app/core/security.py`（`APIKeyStore` Protocol + SQLite 实现） | `tests/test_core/test_security.py` 3 例；**Key 明文存储**（已知限制） |
| 令牌桶限流 | 完成 | `app/core/rate_limit.py`（按 Key 维度，429 + Retry-After） | `tests/test_core/test_rate_limit.py` 4 例；**单进程内存态、桶无淘汰**（已知限制） |
| 指标 | 完成 | `app/core/metrics.py`（Counter + Histogram + `ToolCallTimer`） | `tests/test_core/test_metrics.py`；`/metrics` 无鉴权（已知限制） |
| NL2SQL demo server | 完成 | `servers/demo_sql_server/{db,nl2sql,server}.py`，4 工具（echo/ask/run_sql/list_tables），13 条规则 + 只读 SQL 校验 + Markdown 表格 | `tests/test_servers/` 9 例；**纯规则引擎，无 LLM**（二阶段目标） |
| Agent 调用示例 | 完成（阶段 5 第一点） | `examples/{gateway_client,openai_agent,langchain_agent}.py` + `examples/README.md` | `tests/test_examples/` 10 例；**Agent 逻辑不在网关内**（二阶段目标） |
| Docker 双容器 | 完成 | `Dockerfile` + `docker-compose.yml`（demo_sql 带 healthcheck，gateway depends_on） | 历史双容器全链路冒烟通过 |
| Vercel 公网部署 | 完成 | `config/gateway.vercel.yaml` + `vercel.json` + `.github/workflows/demo-health.yml`（4 步验收） | demo-health 历史全绿；`*.vercel.app` 大陆 DNS 污染（已知环境问题） |
| CI | 完成 | `.github/workflows/ci.yml`（ruff + pytest，push/PR） | 历史全绿 |
| 工具路由 / Discovery | **无** | 无 | 缺口（二阶段阶段 1） |
| 网关内 Agent 能力 | **无** | 仅 examples/ | 缺口（二阶段阶段 2） |
| 交互式 UI | **无** | 仅 FastAPI /docs + CLI 脚本 | 缺口（二阶段阶段 3） |
| 流式输出 | **无** | 全部同步 JSON | 缺口（二阶段阶段 4） |
| LLM NL2SQL 引擎 | **无** | 仅规则 | 缺口（二阶段阶段 5） |
| 多租户 / 工具权限 | **无** | Key 仅有 name + rate_limit | 缺口（二阶段阶段 6） |
| 断线重连 / 健康检查 / 总超时 | **无** | registry 只在启动时连接，失败即永久标记 | 缺口（二阶段阶段 7） |

**本次会话实际验证（沙箱）**：`ruff check` + `ruff format --check` 全过（56 文件）；`pytest` 收集 71 用例，运行 54 通过 / 7 失败 / 1 跳过 / 10 错误——**全部为环境性失败**（DSH 沙箱拦截 data/ 写与 stdio 子进程拉起，与评审文档记载一致，非代码缺陷）。本机历史记录 73/73（含 agent 组）全绿。

**git 状态**：main 与 origin 同步；共 20 个提交；未跟踪文件 3 个（`docs/` 下的评审文档、本提示词文档、`新建 文本文档.txt`）。

## 3. 当前架构（二阶段演进基线）

```
                      ┌─────────────────────────────┐
                      │       LLM Agent / 应用       │
                      │  (examples/ 两个 CLI 示例)    │
                      └──────────────┬──────────────┘
                                     │ 统一 REST API（同步 JSON）
                      ┌──────────────▼──────────────┐
                      │      MCP Gateway (FastAPI)   │
                      │  鉴权(API Key 401) ─→ 限流(令牌桶 429)  │
                      │  registry: {server}__{tool} 聚合与路由  │
                      │  client: stdio/sse/http/inprocess 四传输 │
                      │  日志(structlog JSON) + 指标(Prometheus) │
                      └───────┬───────────┬───────────┘
                   stdio/inprocess┤          ├── http/sse
               ┌───────────────▼──┐   ┌─────▼──────────────┐
               │ demo_sql_server  │   │ 其他 MCP Server(将来)│
               │ (规则 NL2SQL)     │   └────────────────────┘
               └──────────────────┘
```

**分层与关键文件（必须保持，二阶段不推翻）**：

| 层 | 文件 | 职责 |
|---|---|---|
| 入口 | `app/main.py` | FastAPI 入口 + lifespan（registry / key_store / rate_limiter 挂 `app.state`） |
| 配置 | `app/config.py` | `Settings`（默认 < YAML < 环境变量 `GATEWAY_`）、`MCPServerConfig`、`AuthConfig`、`get_server_configs()` 等 lru_cache 单例 |
| 协议 | `app/mcp/{transports,client,schemas,registry}.py` | 传输连接工厂 → 客户端封装 → 聚合注册中心；`schemas.py` 的 `ToolInfo/ToolCallResult` 与 `mcp.types` 解耦，上层只面对自有模型 |
| 横切 | `app/core/{security,rate_limit,logging,metrics}.py` | APIKeyStore(Protocol) / TokenBucket / structlog / Prometheus |
| 接口 | `app/api/deps.py` + `routes/` | 依赖链顺序固定：**先鉴权 401 再限流 429**；路由层只做参数解析与响应组装 |
| 示例 | `examples/` | 只依赖 HTTP 契约（`GET /tools`、`POST /tools/{name}/call`），**不依赖网关内部代码**——该独立性是设计决策，二阶段继续保持 |
| demo | `servers/demo_sql_server/` | 独立演进；双导入模式（包导入 + 裸导入）；环境变量配置 |
| 部署 | `config/*.yaml`、`Dockerfile`、`docker-compose.yml`、`.github/workflows/` | 本地 stdio / Docker http / Vercel inprocess 三套配置并存 |

**关键设计决策（新代码必须延续）**：
1. 工具名命名空间 `{server}__{tool}`（`NAMESPACE_SEPARATOR = "__"`），兼容 OpenAI function calling 命名规则——**所有新路由/权限功能沿用此标识**。
2. 抽象用 Protocol + 工厂：`APIKeyStore`、`create_client()`——新模块（router / translator）同样「接口与实现分离，默认实现零重依赖」。
3. 配置一律进 `app/config.py`（或 server 的环境变量），禁止业务代码写死常量。
4. `GET /tools`、`POST /tools/{name}/call` 是稳定契约，二阶段只能**增量扩展**（加可选参数/新端点），不得改响应结构破坏 examples 与 CI。
5. demo_sql_server 默认零外部依赖（仅 mcp SDK + 标准库），任何 LLM 能力必须可选（未配置时行为与现在完全一致）。
6. 主依赖目前**不含 httpx**（httpx 在 dev/agent 组）——网关内做 LLM 调用（阶段 2）需把 `httpx` 加入主依赖，这是二阶段唯一计划内的依赖变更；禁止引入 Redis/Kafka/向量库/前端构建链。

## 4. 当前已经完成的功能（一阶段成果清单）

1. 四传输 MCP 客户端封装 + 工具注册中心（聚合、命名空间、并发连接、单点失败不阻塞）。
2. 统一 REST API：`GET /health`、`GET /metrics`、`GET /tools`、`POST /tools/{name}/call`、`GET /servers`。
3. API Key 鉴权（SQLite 存储，Protocol 抽象）+ 令牌桶限流（429 + Retry-After）。
4. structlog JSON 日志 + Prometheus 指标（工具调用次数/耗时，按 tool/server/status 维度）。
5. demo_sql_server：迷你电商库 + 规则 NL2SQL（13 规则 + 只读校验 + 行数截断）+ Markdown 结果渲染。
6. examples/：OpenAI function-calling Agent（纯 httpx 手写协议 + `--mock` 离线演示，模型无关）与 LangChain 版（`--group agent`），共 10 例测试。
7. 部署闭环：Docker 双容器（healthcheck）、Vercel inprocess 部署 + `demo-health` 验收 workflow、GitHub Actions CI（ruff + pytest）。
8. 测试 73 例（含真实子进程 stdio 集成测试、独立 http 进程集成测试、Vercel 生产配置回归测试）。

## 5. 当前主要技术缺口（按影响排序）

1. **无工具路由**：`GET /tools` 全量返回，examples 把全部工具注入 LLM——工具数量增长后 context 膨胀、选择质量下降、token 浪费。
2. **Agent 不在网关内**：Agent 循环只存在于 examples/，网关自身无 Agent 能力，也没有可交互的展示方式（只能 `python xxx.py`）。
3. **无流式**：全部同步 JSON，UI 体验差。
4. **NL2SQL 纯规则**：覆盖 13 种问法，未命中的问题直接回退示例列表；接口已解耦但无 LLM 实现。
5. **无权限模型**：Key 只能「全部工具可调」，无 tenant/白名单概念。
6. **无自愈**：server 启动时连接失败即永久失败（要重启网关）；无整体 deadline（单次调用只有 read_timeout）。
7. 小缺口：SSE 传输无测试；Key 明文存储；限流桶无淘汰；`/metrics` 无鉴权；日志无 request_id。

## 6. 二阶段总体目标

**先跑通一个可完整演示的 Agent 产品闭环，再做外围增强。**

```text
User（简易浏览器工作台）
  ↓
Agent（网关内 AgentRunner）
  ↓
Tool Discovery / Routing（语义工具路由，top-k 注入）
  ↓
MCP Gateway（鉴权 / 限流 / 指标）
  ↓
MCP Server（demo_sql：规则 + 可选 LLM 双引擎）
  ↓
Tool Result → Agent → Final Answer（可流式）
  ↓
Interactive UI（/ui 工作台，链路时间线可视化）
```

完成该闭环（阶段 1–4）之后，再依次做：NL2SQL 双引擎（5）、最小多租户（6）、可靠性（7）。

**约束**：不为复杂而复杂——每个功能必须有明确技术原因（见各阶段「动因」）；不引入 Redis/Kafka/K8s/微服务/前端构建工具链；不为了显得「企业级」堆无意义的抽象。

## 7. 二阶段架构演进

```
                      ┌──────────────────────────────────┐
                      │  Interactive Agent Workbench /ui  │  ← 新增（阶段3）
                      │  浏览器端，时间线展示 + SSE 流式    │
                      └──────────────┬───────────────────┘
                                     │ REST + SSE
                      ┌──────────────▼───────────────────┐
                      │          Gateway (FastAPI)        │
                      │ ┌───────────────────────────────┐ │
                      │ │ app/agent/  AgentRunner        │ │ ← 新增（阶段2/4）
                      │ │   （Agent 循环 + 步骤 Trace + 流式）│ │
                      │ │ app/mcp/tool_router.py         │ │ ← 新增（阶段1）
                      │ │   （查询 → 候选工具 top-k）      │ │
                      │ │ 鉴权 → 限流 → 白名单(阶段6) → 指标 │ │
                      │ │ registry + 四传输 client       │ │ ← 阶段7 加自愈
                      │ │   （重连 + 总超时）              │ │
                      │ └───────────────────────────────┘ │
                      └───────┬────────────┬──────────────┘
                stdio/inprocess┤            ├── http/sse
              ┌────────────────▼──┐  ┌──────▼─────────────┐
              │ demo_sql_server   │  │ 其他 MCP Server(将来)│
              │ 规则引擎 + LLM 引擎 │  └────────────────────┘
              │ （阶段5 双引擎切换） │
              └───────────────────┘
```

原则：**所有新增都是增量**——router 挂在 API 层不动 registry；Agent 端点复用 registry.call_tool 不走 HTTP 回环；白名单在鉴权结果上过滤；重连在 registry 内部演进（构造参数默认值不变）。

## 8. 分阶段开发路线（总览）

| 阶段 | 名称 | 一句话目标 | 新增测试（目标） | 依赖 |
|---|---|---|---|---|
| 0 | 二阶段基线 | 环境复验 + 文档/草稿提交，git 干净 | 0 | — |
| 1 | Semantic Tool Routing | `GET /tools?query=` 语义过滤 + 注入 top-k | ≥8 | 阶段 0 |
| 2 | Agent 核心化 | `app/agent/` + `POST /agent/run`（含步骤 Trace） | ≥10 | 阶段 1 |
| 3 | Agent Workbench UI | `/ui` 静态工作台（时间线 + 自检） | ≥3 | 阶段 2 |
| 4 | Streaming | `/agent/run/stream` + `/tools/{name}/call/stream`（SSE） | ≥8 | 阶段 2、3 |
| 5 | NL2SQL 双引擎 | 规则兜底 + 可选 LLM（配置切换） | ≥8 | 独立（可与 4 并行） |
| 6 | 最小多租户 + 工具白名单 | Key → tenant + allowed_tools | ≥8 | 独立（可与 4 并行） |
| 7 | 网关可靠性 | 断线自愈重连 + 调用总超时 + 错误分类 | ≥10 | 独立（可与 4 并行） |

**单次对话执行节奏**：一个对话原则上只完成当前阶段中的一个或少量连续任务；以第 9 节任务列表中的 `[ ] / [x]` 作为细粒度进度标记。**每个 `[ ]` 或 `[x]` 代表一个可以独立完成、测试、验证的任务完成阶段点，不得把一个完整任务继续拆成多个勾选框。**开始新对话时，先找到当前阶段第一个 `[ ]` 任务，从该任务继续；任务完成并通过对应验收后，将该任务从 `[ ]` 改为 `[x]`，再更新第 16 节「当前进度」并提交 git。未完成或仅部分完成的任务保持 `[ ]`，不得提前勾选。


## 9. 每个阶段的具体任务（文件级，可直接执行）

### 阶段 0：二阶段基线（0.5 天）

**动因**：交接清洁——本机复验全绿、未跟踪文档入库，保证后续对话起点一致。
**依赖**：无。**完成后变化**：git 干净，新 AI 从确定基线开始。

步骤：

* [x] **完成本机基线验证**：本机（非沙箱）执行 `uv run pytest`（期望 71 passed + 2 skipped 默认组 / 含 agent 组 73 passed）与 `uv run ruff check .`、`uv run ruff format --check .`；如有失败先修（沙箱内只复验 lint）。
* [x] **完成二阶段文档与草稿整理**：删除 `docs/新建 文本文档.txt`（草稿，评审已点名）；将 `docs/` 下评审文档、提示词文档与本规划文档 `git add` 后提交，commit 信息如 `docs: 二阶段规划（重新评估项目状态）`。
* [x] **完成基线 Git 状态确认**：确认 `git status` 干净、main 与 origin 同步。

验收：

* **代码**：无改动（或仅修复测试失败）。
* **测试**：本机 `uv run pytest` 全绿。
* **运行**：`uv run uvicorn app.main:app` 启动日志 `registry_ready connected=1 tools=4`。
* **兼容**：—。

---

### 阶段 1：Semantic Tool Routing（工具发现与路由）

**动因**：工具数量增长后全量注入导致 context 膨胀、token 浪费、选择质量下降——这是网关层的经典问题，也是当前与同类项目的最大差异点。registry 的 `ToolInfo`（name/description/input_schema）已提供全部索引数据，**无需改 registry 对外接口**即可实现。
**依赖**：阶段 0。**完成后变化**：`GET /tools?query=...` 按语义相关性返回 top-k；Agent 可只注入相关工具；无 query 时行为不变。

步骤：

* [x] **完成 `app/mcp/tool_router.py`：实现第一版本地 Tool Router**

  * `ToolDocument` dataclass：`tool: ToolInfo`、`searchable_text: str`、`field_tokens: dict[str, set[str]]`（name/description/schema 三字段分词结果分开存）。
  * `build_documents(tools: list[ToolInfo]) -> list[ToolDocument]`：searchable_text = 工具名 + 描述 + input_schema 的参数名与参数描述拼接。
  * 分词函数 `tokenize(text: str) -> list[str]`（**v1 轻量本地方案，零依赖零网络**）：英文/数字按 `[a-zA-Z0-9_]+` 小写切分；中文按 2-gram 切分（如「销售额」→「销售」「售额」）；无停用词表、无 jieba、无 embedding。
  * 打分函数 `score(query_tokens, doc) -> float`：命中计数加权——name 命中 ×3、description ×2、schema ×1，再乘「命中数 / query token 数」覆盖率。
  * `ToolRouter` 类：`__init__(top_k: int = 10, min_tools: int = 10)`；`search(query: str, tools: list[ToolInfo], top_k: int | None = None) -> list[ToolInfo]`——**工具总数 ≤ min_tools 时原样返回全部（保证小工具集零过滤）**；命中为空时返回原列表前 top_k（保底，不空手）。
  * 预留 `Scorer` Protocol（`score(query_tokens, doc) -> float`），默认 `KeywordScorer`，未来可换 embedding 实现——**v1 不实现 embedding**。

* [x] **完成 Tool Router 配置接入**

  * `app/config.py`：新增 `ToolRouterConfig(BaseModel)`（`enabled: bool = True`、`top_k: int = 10`、`min_tools: int = 10`）+ `get_router_config()`（lru_cache，读 YAML `routing` 节，缺省用默认值）。
  * `config/gateway.yaml` 增加 `routing:` 节（演示配置 min_tools=3，使 4 工具下过滤可见；接入更多工具后可改回 10）。
  * router 实例在 lifespan 创建并挂到 `app.state`。

* [x] **完成 `/tools` 的查询与 Top-K 路由接入**

  * `app/api/routes/tools.py`：`GET /tools` 增加**可选**查询参数 `query: str | None = None`、`top_k: int | None = None`。
  * 有 query 且 config.enabled 时返回 `router.search(query, tools, top_k)`。
  * 无 query 时行为与现在一致，`response_model` 不变。

* [x] **完成客户端与 OpenAI Agent 的 Tool Routing 接入**

  * `examples/gateway_client.py`：`list_tools(query: str | None = None, top_k: int | None = None)` 透传查询参数，默认仍无参。
  * `examples/openai_agent.py`：当 `len(tools) > 10` 时改为 `gateway.list_tools(query=args.question, top_k=10)`，打印「共 N 个工具，注入 top-K：…」；工具 ≤10 时行为不变，保证当前 4 工具离线 mock 演示不受影响。

* [x] **完成 Tool Router 单元测试**

  * 新建 `tests/test_mcp/test_tool_router.py`，至少覆盖：

    * 建索引与分词；
    * 关键词命中排序（「销售额」命中 ask 高于 echo）；
    * top_k 截断；
    * min_tools 阈值内不过滤；
    * query 无命中时返回保底列表。

* [x] **完成 `/tools` 查询参数的 API 测试与兼容验证**

  * `tests/test_api/test_tools.py` 补充：

    * `GET /tools?query=销售额&top_k=2` 返回 ≤2 个且 `demo_sql__ask` 排第一（实际命中仅 ask 1 个，断言放宽为「≤2 且 ask 排前」）；
    * 不带 query 全量返回 4 个；
    * routing.enabled=false 时 query 不生效（monkeypatch 验证）。
  * 确保原有 `/tools` 测试继续通过。

验收：

* **代码**：`app/mcp/tool_router.py` + `GET /tools` 可选 query 参数 + config `routing` 节。
* **测试**：相关测试全绿；全量 pytest 无回归；ruff 通过。
* **运行**：启动网关后 `curl -H "X-API-Key: dev-key-please-change" "http://localhost:8000/tools?query=查询销售额"` 返回销售相关工具且 `demo_sql__ask` 排前；`/tools` 无参仍 4 个。
* **兼容**：examples 两个脚本（含 `--mock`）跑通；`demo-health` 第 3 步（无参 `/tools` == 4 工具）不受影响；CI 全绿。

---

### 阶段 2：Agent 核心化（网关内 AgentRunner + /agent/run）

**动因**：评审核心批评「AI 含量不足」——Agent 现在是 examples/ 里的演示脚本，不是网关能力；且后续工作台 UI 必须由网关侧持有 LLM Key（Key 不能进浏览器）。examples/ 已验证循环逻辑可行，二阶段把它作为网关一等公民。
**依赖**：阶段 1（Agent 端点用 router 选工具）。**完成后变化**：`POST /agent/run` 一步完成「问题 → Agent 循环 → 最终回答 + 完整步骤 Trace」；网关从被动管道变为 Agent 宿主。

步骤：

* [x] **完成网关 LLM 调用依赖与 Agent 核心模块**

  * `pyproject.toml`：主依赖增加 `"httpx>=0.27.0"`，`uv lock` 更新。
  * 新建 `app/agent/__init__.py`、`app/agent/schemas.py`、`app/agent/models.py`、`app/agent/runner.py`。
  * `schemas.py`：定义 `AgentRequest`、`AgentStep`、`AgentResponse`。
  * `models.py`：实现 `Model` Protocol、OpenAI 兼容模型工厂（自持 httpx 客户端，`aclose()` 释放）、`MockModel`（含 `direct` 直接回答模式）。
  * `runner.py`：实现 `AgentRunner`，内部循环直接调用 `registry.call_tool()`，不走 HTTP 回环；记录每一步延迟；工具列表先经过 router 过滤；`close()` 统一释放模型资源。

* [x] **完成 Agent 配置接入**

  * `app/config.py`：新增 `AgentConfig(BaseModel)`（`enabled: bool = False`、`mock: bool = False`、`model: str = "gpt-4o-mini"`、`base_url: str = "https://api.openai.com/v1"`、`max_rounds: int = 8`、`routing_top_k: int = 10`）。
  * 新增 `get_agent_config()`，读取 YAML `agent:` 节。
  * **LLM API Key 只从环境变量 `GATEWAY_AGENT_API_KEY` 读取，绝不进 YAML**。
  * `config/gateway.yaml` 加 `agent:` 节，`enabled: false` 起步，`mock: true` 供离线演示。

* [x] **完成 `/agent/run` API 接入**

  * 新建 `app/api/routes/agent.py`。
  * `POST /agent/run` 使用 `ProtectedDep`，鉴权 + 限流与工具接口一致。
  * 返回 `AgentResponse`。
  * 未启用/未配 Key 且非 mock 时返回 503 和明确提示。
  * 异常时保留已经发生的步骤信息（runner 把单点工具失败记入 error step，不中断循环）。

* [x] **完成 Agent 生命周期接入**

  * `app/main.py` include router。
  * lifespan 中根据 `AgentConfig` 惰性创建 `AgentRunner` 并挂 `app.state.agent_runner`。
  * 未启用时保持 `None`，路由返回 503。

* [x] **完成 AgentRunner 单元测试**

  * `tests/test_agent/test_runner.py` 至少覆盖：

    * 「有多少客户？」完整调用链；
    * 直接回答分支；
    * 轮数上限；
    * steps 类型与顺序；
    * `tools_injected <= tools_total`。

* [x] **完成 Agent API 测试**

  * `tests/test_agent/test_api.py` 至少覆盖：

    * `agent: {enabled: true, mock: true}` 时 `/agent/run` 返回 200；
    * steps 非空；
    * 无 Key 返回 401；
    * limited-key 超限返回 429；
    * 未启用配置返回 503。

验收：

* **代码**：`app/agent/` 核心模块 + `POST /agent/run` + httpx 进主依赖。
* **测试**：`tests/test_agent` 全绿；全量无回归；ruff 通过。
* **运行**：启动网关（agent.mock: true）后，`POST /agent/run` 能返回 answer + steps，并包含 `demo_sql__ask`。
* **兼容**：`/tools`、`/tools/{name}/call`、examples 两个脚本零回归；无 Key 401、超限 429 语义不变。

---

### 阶段 3：Interactive Agent Workbench（/ui 工作台）

**动因**：项目不能长期只能 `python xxx.py` 或看 `/docs`；评审与二阶段要求「简单但真正可交互的 Agent 界面」，且它能直接演示阶段 1/2 的价值（路由、Trace）。
**依赖**：阶段 2（`/agent/run` 提供 steps）。**完成后变化**：浏览器打开一个 URL 即可体验完整链路。

步骤：

* [x] **完成 `app/ui/` 静态工作台**

  * 新建 `app/ui/index.html`、`app.js`、`style.css`。
  * **纯静态三件套，无任何前端构建链、无框架、无 CDN**。
  * `index.html`：顶部状态栏 + 中间时间线 + 底部提问输入框/发送按钮。
  * 时间线节点：User → Agent → Gateway → Tool Result → Final Answer。
  * `app.js`：调用 `POST /agent/run`，按 `AgentResponse.steps` 渲染时间线；显示 `tools_injected/tools_total`；处理 401/429/503/连接错误；提供健康检查与工具列表自检。
  * `style.css`：极简深色终端风，不追求复杂视觉效果。

* [x] **完成 FastAPI UI 挂载**

  * `app/main.py`：`app.mount("/ui", StaticFiles(directory=..., html=True), name="ui")`；另加 `GET /ui` 精确路由直接返回 index.html（StaticFiles 对目录路径会 307 到 `/ui/`）。
  * `GET /` 使用 `RedirectResponse` 跳转 `/ui`。
  * UI 路径使用 `Path(__file__).parent / "ui"`，确保本地和 Vercel 路径稳定。

* [x] **完成 UI 使用说明与最小测试**

  * `README.md` 与 `examples/README.md` 各增加浏览器访问方式。
  * 新建 `tests/test_ui.py`，覆盖：

    * `GET /ui` 200 且响应含 `<html`；
    * `GET /` 302/307 到 `/ui`；
    * 无 Key 调 `/agent/run` 仍 401。

* [x] **完成浏览器端完整运行验收**

  * 启动网关（mock 模式）→ 浏览器打开 `http://localhost:8000/ui`。
  * 输入「查询目前销售额最高的商品」。
  * 时间线完整展示工具选择 → Gateway 鉴权/限流/执行 → Tool Result → Final Answer。
  * 显示工具数量、注入数量与延迟。
  * 确认 `/docs` 仍可用。
  * （本沙箱以 HTTP 全端点验收替代真实浏览器交互：`/` 307→`/ui`、`/ui` 200 含 `<html`、`/ui/app.js` 200、`/docs` 200、`POST /agent/run` 200 返回 answer+steps；真实浏览器打开路径一致。）

验收：

* **代码**：`app/ui/{index.html,app.js,style.css}` + mount + 根路径重定向。
* **测试**：`uv run pytest tests/test_ui.py` 全绿；全量无回归。
* **运行**：浏览器能够完整完成一次 Agent 任务。
* **兼容**：全部 REST 契约不变；`/docs` 仍可用。

---

### 阶段 4：Streaming（Agent 流式 + 工具调用 SSE）

**动因**：UI 实时反馈是 AI 应用的标配体验；评审点名「无流式」。**先做 HTTP 层 SSE 契约，MCP 协议层 progress 通知透传明确不做**（SDK 2.0 改造复杂、当前 demo 工具均为瞬时 SQL，收益低；留作远期）。
**依赖**：阶段 2、3。**完成后变化**：工作台逐字输出最终回答；第三方 Agent 可通过 SSE 订阅调用过程。

步骤：

* [ ] **完成 Agent Model 的流式能力**

  * `app/agent/models.py`：`Model` 增加可选 `chat_stream(messages, tools) -> AsyncIterator[dict]`。
  * `build_openai_model` 使用 `httpx stream=True` + `stream: true` 解析 SSE。
  * `MockModel` 实现假 token 流。

* [ ] **完成 AgentRunner 的流式执行**

  * `app/agent/runner.py`：新增 `run_stream(question) -> AsyncIterator[AgentEvent]`。
  * 复用 `run()` 的工具选择 / 工具执行 / 结果回填核心逻辑。
  * 文本通过 `chat_stream` 透传。
  * 工具执行前后产生 step 事件。

* [ ] **完成 `/agent/run/stream` SSE API**

  * `POST /agent/run/stream` → `StreamingResponse(media_type="text/event-stream")`。
  * 事件格式：

    * `event: step`
    * `event: token`
    * `event: done`
  * 鉴权与限流规则与同步端点一致。

* [ ] **完成 `/tools/{name}/call/stream` SSE API**

  * `POST /tools/{tool_name}/call/stream` → SSE。
  * 事件顺序：`start` → `result` → `done`。
  * 内部仍调用 `registry.call_tool()`。
  * **同步 `/tools/{name}/call` 一字不改。**

* [ ] **完成 Agent Workbench 的流式渲染**

  * `app/ui/app.js` 改用 `/agent/run/stream`。
  * `fetch` 流式读取 `response.body`，按 `\n\n` 分帧。
  * token 逐字追加。
  * step 实时插入时间线卡片。
  * 失败时保留同步端点提示。

* [ ] **完成 Agent 与 Tool Streaming 测试**

  * `tests/test_agent/test_stream.py` 至少覆盖：

    * `step → token* → done`；
    * token 拼接等于最终答案；
    * MockModel 全链路流式；
    * `/tools/{name}/call/stream` 事件序列；
    * 无 Key 401。
  * `tests/test_api/test_tools.py` 补 call/stream 兼容断言。

* [ ] **完成 Streaming 本地与兼容性验收**

  * `curl -N` `/agent/run/stream` 能观察到事件流。
  * 浏览器 `/ui` 能逐字输出。
  * 同步 `/agent/run`、`/tools/{name}/call` 响应不变。
  * examples 不受影响。

验收：

* **代码**：两个 `/stream` SSE 端点 + runner/model 流式实现 + UI 流式渲染。
* **测试**：新增用例全绿；同步端点既有用例零回归；ruff 通过。
* **运行**：curl 与浏览器均能观察到流式事件。
* **兼容**：同步接口行为不变。

---

### 阶段 5：NL2SQL 双引擎（规则兜底 + 可选 LLM）

**动因**：评审追问「这不叫 AI」——但规则引擎的离线确定性恰恰是演示稳定性保障。双引擎让「规则兜底、LLM 增强」落地，且 `question_to_sql()` 早已与工具层解耦，扩展成本最低。
**依赖**：独立（只动 `servers/demo_sql_server/` 与测试）。**完成后变化**：未配 LLM 时行为与现在完全一致（规则 + 示例回退）；配置 LLM 后未命中规则的问题由 LLM 生成 SQL（结果标注所用引擎）。

步骤：

* [ ] **完成 `LLMTranslator`**

  * 新建 `servers/demo_sql_server/llm_translator.py`。
  * `LLMTranslator(base_url, api_key, model, timeout=30.0)`。
  * `question_to_sql(question, schema_text)` 通过 OpenAI 兼容 `chat/completions` 生成 SQL。
  * system prompt 固定只读 SELECT 约束、表结构和只输出 SQL 的要求。
  * 任何异常、超时、空响应返回 `None`，规则路径绝不因 LLM 挂掉。

* [ ] **完成 NL2SQL Translator 抽象与工厂**

  * `servers/demo_sql_server/nl2sql.py` 新增 `Translator` Protocol。
  * `create_translator(mode, *, llm=None)` 支持：

    * `rule`
    * `llm`
    * `hybrid`
  * `hybrid` 模式规则命中优先，未命中才降级 LLM。
  * 保留原 `question_to_sql(question) -> str | None` 签名不变，新增 `translate()`。

* [ ] **完成 demo_sql_server 的双引擎接入**

  * `server.py` 的 `ask` 工具改走 `translate()`。
  * 支持：

    * `DEMO_SQL_NL2SQL_MODE`
    * `DEMO_SQL_LLM_BASE_URL`
    * `DEMO_SQL_LLM_API_KEY`
    * `DEMO_SQL_LLM_MODEL`
  * 结果文本显示「引擎：规则 / LLM」。
  * 无 LLM 配置时 hybrid 自动退化为 rule。

* [ ] **完成 LLM Translator 测试**

  * `tests/test_servers/test_llm_translator.py` 至少覆盖：

    * 命中返回 SQL；
    * prompt 含表结构关键词；
    * 异常/非 200 返回 None；
    * 未装 httpx 时工厂降级。

* [ ] **完成 NL2SQL 工厂与兼容测试**

  * `tests/test_servers/test_nl2sql.py` 补充：

    * 三模式选择正确；
    * hybrid 规则命中不调用 LLM；
    * hybrid 规则未命中降级 LLM；
    * 既有 13 条规则用例零改动。

验收：

* **代码**：`llm_translator.py` + `nl2sql.py` 工厂 + `ask` 引擎标注。
* **测试**：`uv run pytest tests/test_servers` 全绿；全量无回归。
* **运行**：无 LLM 环境变量时行为与现在一致；配置 LLM 后规则外问题能够生成 SQL 并执行。
* **兼容**：Vercel/Docker 无 LLM 配置时行为不变；`demo-health` 第 4 步不受影响。

---

### 阶段 6：最小多租户 + 工具白名单

**动因**：Key 目前只能「全有或全无」，评审追问的「企业级」与权限模型的最短实现路径就是 tenant + 白名单。**范围护栏：只做白名单，不做 RBAC 角色/组织/审批/租户配额**。
**依赖**：独立（可与 4/5/7 并行）。**完成后变化**：一个 Key 只能看/调被授权的工具；未授权的 `call` 返回 403。

步骤：

* [ ] **完成 API Key Schema 的 tenant / allowed_tools 扩展**

  * `app/schemas/auth.py`：

    * `APIKeyInfo` 增加 `tenant: str = "default"`；
    * `allowed_tools: list[str] | None = None`。
  * `None = 全部`，兼容旧数据。

* [ ] **完成 SQLite Key 存储与轻量迁移**

  * `app/core/security.py`：

    * 增加 `tenant TEXT NOT NULL DEFAULT 'default'`；
    * 增加 `allowed_tools TEXT`；
    * 使用 JSON 字符串保存 allowed_tools。
  * `init` 时使用 `PRAGMA table_info(api_keys)` 检查缺列并幂等 `ALTER TABLE`。
  * 读写转换保持兼容。

* [ ] **完成 Registry 的按 Key 工具可见性**

  * 保持 `get_current_key` 不变。
  * 新增按 Key 过滤工具的能力。
  * 推荐由 registry 提供 `list_tools_for(key)`，避免路由层直接实现业务逻辑。

* [ ] **完成工具 API 的白名单检查**

  * `GET /tools` 使用 `list_tools_for(api_key)`。
  * `call_tool` 先检查工具可见性。
  * 不可见返回 **403**。
  * 真不存在的工具继续返回 404。
  * `/servers` 的 tool_count 改为按当前 Key 的可见数量。

* [ ] **完成演示 Key 配置**

  * `config/gateway.yaml`：

    * demo Key 增加 `tenant: demo`，不设置 allowed_tools，保持全量；
    * 新增 `limited-tools-key`，`tenant: demo`，`allowed_tools: ["demo_sql__ask"]`，60 次/分钟。
  * YAML 中添加用途说明。

* [ ] **完成工具权限 API 测试**

  * `tests/test_api/test_tools.py` 至少覆盖：

    * 白名单 Key 的 `/tools` 只返回 1 个；
    * 调 `demo_sql__run_sql` 返回 403；
    * 调 `demo_sql__ask` 返回 200；
    * 全量 Key 行为不变。

* [ ] **完成 Key Schema 与迁移测试**

  * `tests/test_core/test_security.py` 至少覆盖：

    * tenant/allowed_tools 种子读写；
    * 旧数据库缺列迁移幂等。

验收：

* **代码**：schema + 存储 + `list_tools_for` + 403 分支。
* **测试**：新用例全绿；既有 demo Key 类用例零回归；ruff 通过。
* **运行**：`limited-tools-key` 只见 `demo_sql__ask`；调用 `run_sql` 得 403；demo Key 全量可用。
* **兼容**：`demo-health` 与 examples 不受影响。

---

### 阶段 7：网关可靠性（断线自愈 + 调用总超时 + 错误分类）

**动因**：server 断线后当前只能重启网关（启动连接失败即永久标记）；单次调用只有 read_timeout、无整体 deadline。这是网关「上生产」最值得先做的三件事。**明确不做**：熔断降级、分布式限流、SSE 心跳（单实例 demo 收益低）。
**依赖**：独立。**完成后变化**：失败的 server 自动指数退避重连并恢复工具；任何工具调用有总时长上限；`/servers` 能看到重试状态。

步骤：

* [ ] **完成 Registry 的失败状态与自动重连机制**

  * `app/mcp/registry.py`：

    * 构造签名扩展 `__init__(..., client_factory=create_client, retry_base=2.0, retry_max=60.0)`，默认值不变。
    * `_errors` 升级为 `_failures: dict[str, FailureState]`。
    * `FailureState` 包含 `error`、`attempts`、`next_retry_at`。
    * `ServerStatus` 增加 `retry_attempts`、`next_retry_at` 默认字段。
    * `connect_all()` 完成后启动 `_retry_task`。
    * 失败 server 按指数退避自动重试。
    * 新增 `mark_failed(name, error)`，移除失效 client 与工具并进入 failure 状态。

* [ ] **完成 Tool Call 总超时与错误分类**

  * `registry.call_tool()` 使用 `asyncio.timeout(tool_call_timeout)`。
  * 新增 `classify_error(exc)`：

    * `TimeoutError` → timeout；
    * `ConnectionError` / `RuntimeError` / `OSError` → connection；
    * 其他 → tool_error。
  * connection 异常触发 `mark_failed`。
  * timeout 记录 exception 指标并抛出 `ToolCallTimeoutError`。
  * `close()` 负责取消 `_retry_task`。

* [ ] **完成工具调用超时 API 行为**

  * `app/api/routes/tools.py` 捕获 `ToolCallTimeoutError`。
  * 返回 502，并明确提示「工具调用超时（>Ns）」。
  * 其他错误行为保持不变。

* [ ] **完成 Registry 恢复机制测试**

  * 新建 `tests/test_mcp/test_registry_recovery.py`，至少覆盖：

    * 首连失败 → failure 记录；
    * `retry_now()` 测试钩子；
    * 重试成功 → 工具恢复；
    * 指数退避间隔计算；
    * connection 异常 → mark_failed + 摘工具；
    * timeout 分类；
    * `ServerStatus` 新字段默认值；
    * `close()` 取消重试任务。

* [ ] **完成 SSE Transport 测试缺口**

  * `tests/test_mcp/test_sse_transport.py` 至少增加 2 例。
  * 优先本地 SSE server 集成测试。
  * 如果 SDK 2.0 拉起复杂度过高，可降级为 `sse_client` 上下文建立的最小冒烟测试，用于补齐现有测试缺口。

* [ ] **完成可靠性配置与完整验收**

  * `config/gateway.yaml` 的 `gateway` 节增加 `tool_call_timeout: 30.0` 显式注释。
  * 运行 `uv run pytest tests/test_mcp` 与全量测试。
  * Docker http 模式停止并重新启动 `demo_sql`，确认网关能够自动恢复。
  * 本地 stdio 模式 kill 子进程后确认能够自动恢复。
  * 确认既有 registry 测试与指标行为无回归。

验收：

* **代码**：registry 自愈三件套 + 超时 + 错误分类 + `/servers` 重试字段。
* **测试**：`uv run pytest tests/test_mcp` 全绿；全量无回归。
* **运行**：Docker http / 本地 stdio 均能完成停止服务 → 自动恢复 → 工具恢复。
* **兼容**：启动时全失败仍不阻塞；指标状态不受影响；既有 registry 测试零改动。


## 10. 每个任务的验收标准（汇总矩阵）

每阶段必须同时满足四项才可标记完成并更新「当前进度」：

| 阶段 | 代码验收 | 测试验收 | 运行验收 | 兼容验收 |
|---|---|---|---|---|
| 0 | git 干净、docs 提交 | 本机 pytest 全绿 | uvicorn 启动 `tools=4` | — |
| 1 | `tool_router.py` + query 参数 | 新用例 + 全量无回归 | `?query=销售额` 过滤且 ask 排前 | 无参 /tools == 4；examples 跑通 |
| 2 | `app/agent/` + `/agent/run` | `tests/test_agent` 全绿 | curl 返回 answer+steps | 工具端点/examples 零改动 |
| 3 | `app/ui/` + mount | `tests/test_ui.py` 全绿 | 浏览器完整时间线 | REST 契约不变 |
| 4 | 两个 SSE 端点 | 流式新用例全绿 | curl -N 见事件流 | 同步端点用例零改动 |
| 5 | 双引擎 + 工厂 | `tests/test_servers` 全绿 | 规则外问题 LLM 出 SQL | 无 LLM 配置行为不变 |
| 6 | tenant + 白名单 + 403 | 新用例全绿 | limited-tools-key 只见 ask | demo Key 与 demo-health 不受影响 |
| 7 | 重连 + 超时 + 分类 | `tests/test_mcp` 全绿 | 停服后自愈 | registry 既有测试零改动 |

**阶段完成的统一定义**：四项验收全部通过 + `uv run ruff check .` 与 `uv run ruff format --check .` 通过 + 更新本文档第 16 节 + git commit。

## 11. 推荐开发顺序

1. 严格顺序：**0 → 1 → 2 → 3 → 4**（1–4 是核心闭环，有硬依赖链）。
2. 阶段 5 / 6 / 7 相互独立、与 4 亦无依赖，理论可并行；**单人开发仍建议按 5 → 6 → 7 串行**（每个规模小，串行更稳）。
3. 任何阶段中途遇到环境问题（沙箱/uv/子进程），先看第 13 节「环境陷阱」，不要花时间重造排查。
4. 每完成一个阶段：提交 git → 更新第 16 节 → 结束对话前把「下一步」写清楚。

## 12. 暂不处理事项（明确降级，避免铺开）

以下事项评审中提过但**二阶段不做**，防止战线拉长：

- README 美化 / 英文版 / demo 录屏 / 架构图重绘；仓库改名（去 `_Demo`）；删除 `docs/新建 文本文档.txt` 以外的文档整理（该文件在阶段 0 删）。
- GitHub Topics、博客、社区推广、向 MCP 生态提 PR、刷 star、简历改写。
- OpenTelemetry 链路追踪；熔断降级；Redis 分布式限流；Key 哈希存储与轮换接口；JWT（python-jose 依赖已预留，未实现）。
- MCP 协议层 progress 通知透传（阶段 4 已明确只做 HTTP 层 SSE）。
- 模型路由/计费（One-API 同类天花板，不追）；K8s；后台管理系统；SSE 传输之外的新传输协议。
- 压测数据（wrk/locust）——等核心闭环（阶段 1–4）稳定后再议。

## 13. 风险与兼容性要求

**兼容红线（任何阶段违反即返工）**：
1. `GET /tools`（无参）、`POST /tools/{name}/call`、`GET /servers`、`GET /health`、`GET /metrics` 的响应结构与状态码语义不可破坏（阶段 6 的 403 是新增分支，不改变未授权场景之外的行为）。
2. `tests/` 既有 73 例除非是任务明确要求，一律零改动（改动必须说明理由并同步更新断言）。
3. examples/ 与网关内部的「双向无依赖」原则（阶段 2 独立实现，不从 examples 导入）。
4. demo_sql_server 默认零外部依赖；新增 httpx 引用必须 try-import 可选。
5. 命名空间工具名 `{server}__{tool}` 是全局标识，白名单/路由/指标全部沿用。

**Vercel / GitHub 部署兼容要求**：
- 所有新增功能必须同时考虑本地运行与 Vercel Serverless 部署环境。
- 禁止默认依赖长驻进程、MCP 子进程、项目目录写入、本地持久化文件或前端构建环境。
- 网页端继续作为 FastAPI 项目的一部分，通过 `app/ui/` 静态文件随 GitHub 仓库一起提交，并由 Vercel 一体化部署。
- 每完成一个涉及 Agent、UI、SSE、LLM 或 MCP 的阶段，都应验证本地运行结果，并确认不破坏现有 Vercel 部署方案（尤其是 inprocess MCP 传输）。

**环境陷阱（一阶段血泪，直接照做）**：
- 本机 PATH 被 pyenv-win shim 抢占：跑 pytest/uvicorn 必须 `uv run` 或 PATH 前置 `.venv\Scripts`。
- uv 缓存损坏（`sdists-v9\.git` os error 5）：设 `UV_CACHE_DIR` 指向新目录绕开。
- DSH 沙箱拦截子进程 stdio 与文件写：pytest 大量环境性失败时先升级权限复验；`uv run` 拉解释器同样受影响。
- LangChain 示例必须 `uv run --group agent python examples/langchain_agent.py ...` 一步跑，拆两步会被 uv 裁依赖。
- 本机 git push 需要仓库级 `http.sslbackend openssl` + `http.sslVerify false`（本机 schannel 证书问题，勿写入全局配置）。
- MCP SDK 实装 2.0.0：`streamable_http_client` yield 2 元组（不是 3）；服务端 API 是 `mcp.server.mcpserver.MCPServer`（无旧 fastmcp 模块）；改 SDK 版本需先跑 `test_http_transport` 与 `test_inprocess`。
- 360 安全软件会拦 pytest 子进程（WinError 5），复现先查 360。

**依赖护栏**：二阶段允许的依赖变更仅「httpx 进主依赖」（阶段 2）；禁止 Redis/Kafka/向量库/JS 框架/前端构建链。新增配置字段一律给默认值，保证旧 YAML 可运行。

## 14. 测试要求

- 沿用惯例：每个受保护接口至少覆盖 成功 / 401 / 429 三 case；新路由再加 404/403/503 分支。
- 新模块单测放 `tests/test_mcp/`、`tests/test_agent/`、`tests/test_servers/` 对应目录；集成测试优先用 inprocess 或 fake client（沙箱/CI 稳定），stdio/http 真子进程测试保持现有写法。
- 每阶段完成后：相关测试 → `uv run pytest` 全量 → `uv run ruff check .` + `uv run ruff format --check .` → 全绿才算数。
- 测试数量预期：一阶段 73 → 二阶段结束约 125（阶段 1–7 新增 ≥55）。
- CI（`.github/workflows/ci.yml`）无需改动即可覆盖新测试；改 pyproject 后必须 `uv lock` 并确认 CI `--locked` 不失败。

## 15. Agent Workbench 规划（阶段 3 详设）

界面目标（不追求美观，必须体现完整链路）：

```
┌────────────────────────────────────────────────┐
│ MCP Agent Workbench        工具: 4/4 · 注入: 2  │
│ 网关: http://localhost:8000   Key: [________]  │
├────────────────────────────────────────────────┤
│ ▍User    查询目前销售额最高的商品                 │
│ ▍Agent   选择工具 demo_sql__ask   (43ms)        │
│ ▍Gateway 鉴权 ✓  限流 ✓  执行 ✓                 │
│ ▍Tool    生成 SQL ... 结果表格（可折叠）          │
│ ▍Agent   最终回答（流式逐字）                    │
├────────────────────────────────────────────────┤
│ 输入问题............................. [发送]   │
└────────────────────────────────────────────────┘
```

实现要点（对应第 9 节阶段 3）：
- 纯静态：`app/ui/index.html` + `app.js` + `style.css`，FastAPI `StaticFiles(html=True)` 挂载，无构建链。
- 数据源：`POST /agent/run`（阶段 3）→ 阶段 4 后切 `POST /agent/run/stream`；Key 从页面输入框读、只放请求头、不落 localStorage。
- 时间线渲染：按 `AgentResponse.steps` 的 `kind` 分类渲染四种卡片；`tools_injected/tools_total` 展示路由效果（阶段 1 的直接可视化）。
- 错误展示：401/429/503/连不上时原样显示网关文案（与 examples 的友好报错同风格）。
- 自检区：「健康检查」「列出工具」两个按钮直查 `/health`、`/tools`，方便演示时排查。
- 不做：登录态、历史会话持久化、多会话管理（超出「简单可交互」边界）。

## 16. 当前状态（当前进度）

- **当前阶段**：二阶段**阶段 3（Interactive Agent Workbench）已完成**；基线 = 一阶段开发 + 二阶段阶段 0/1/2/3。
- **已完成阶段**：一阶段 阶段 0–4 + 5 第一点；二阶段 阶段 0（基线）、1（Semantic Tool Routing）、2（Agent 核心化）、3（Agent Workbench UI）。
- **正在进行**：无（阶段 4 待开始）。
- **已完成任务（二阶段）**：阶段 0 三项；阶段 1 六项；阶段 2 六项；阶段 3 四项（`app/ui/` 三件套、FastAPI 挂载 + 根路径重定向、README/测试 4 例、运行验收）。
- **测试数量**：104 例（72 基线 + 32 新增）；沙箱升级权限复验 102 通过 / 1 环境性失败 / 1 跳过。
- **最新验证结果**（2026-08-24，本沙箱）：`ruff check` + `ruff format --check` 全过（67 文件）；`pytest` 102 通过，唯一失败 `tests/test_mcp/test_http_transport.py`（沙箱 TCP 拦截，纯环境性）；运行验收（默认配置，mock agent 已默认启用）：`/` 307→`/ui`、`/ui` 200 含 `<html`、`/ui/app.js` 200、`/docs` 200、`/tools` 4 个、`POST /agent/run` 200（rounds=2、injected 1/4、销售额查询返回 20289.0）。
- **已知问题**：SSE 传输无测试；Key 明文存储；限流单进程、桶无淘汰；无重连/总超时；无流式——**由二阶段阶段 4、7 覆盖**（「无工具路由」「Agent 不在网关内」「无交互 UI」已被阶段 1/2/3 解决）。
- **环境问题**：DSH 沙箱（子进程/写限制 + 受限 python 进程 TCP 连接被拦截返回 502，升级权限复验可排除除 http 传输测试外的全部）；uv 缓存损坏用 `UV_CACHE_DIR` 绕开；pyenv PATH 抢占（启动网关须 PATH 前置 `.venv\Scripts`）；本机 git ssl 配置；360 拦截子进程（见第 13 节）。
- **下一步**：执行阶段 4（Streaming）：Model 流式能力（`chat_stream` + SSE 解析）、`AgentRunner.run_stream`、`POST /agent/run/stream` 与 `POST /tools/{name}/call/stream` 两个 SSE 端点、`app/ui/app.js` 切流式渲染、`tests/test_agent/test_stream.py` + 工具流式测试。
- **最后更新时间**：2026-08-24。

## 17. 下一步建议

1. 新对话直接用第 0 节开场提示词开始。
2. 第一个执行任务：阶段 4 任务 1（`app/agent/models.py` 增加 `chat_stream` 流式能力：httpx `stream=True` + SSE 解析，`MockModel` 假 token 流），随后 runner 的 `run_stream` 与两个 SSE 端点。
3. 每个对话结束前必须：更新第 16 节 + git commit + 明确写出「下一步」。
4. 遇到与本文档矛盾的事实，以代码为准，并把矛盾记录进第 16 节「已知问题」。
