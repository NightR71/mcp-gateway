# MCP Gateway 项目评审与优化建议（HR × 技术面试官 双视角）

> 评审日期：2026-08-20 ｜ 评审对象：`D:\New_Project`（GitHub: `NightR71/MCP_Gateway_Demo`，main 分支，约 20 个提交）
> 评审方法：通读规划文档与核心代码（main / config / core / mcp / api / demo server / README / pyproject / git 历史），沙箱内复跑测试，并调研 GitHub 与中文技术圈同类项目后成文。
> 测试复验说明：沙箱环境受限（文件写权限 + stdio 子进程拉起被禁），复测结果为 48 通过、5 失败、10 错误——**全部为环境性失败**（`PermissionError` 写 data/ 与 SQLite、子进程拉起失败导致 registry 0 工具），非代码缺陷。请在本机以 `uv run pytest` 复验（项目文档与 CI 记录均为 63/63 全绿）。

---

## 一、结论速览（TL;DR）

**三句话结论：**

1. **技术完成度：应届生里偏上**——不是"Hello World 级别"的项目。三传输客户端、进程内 inprocess 改造、63 个测试、CI、Docker 双容器、真实部署踩坑（Vercel 子进程依赖缺失、vercel.app 大陆 DNS 污染），这些是硬功夫。
2. **选题撞车率：极高**——"MCP 网关（统一接入 + 鉴权 + 限流 + 指标）"是 2025–2026 年最拥挤的教程/开源赛道之一。GitHub 上同名同类项目一抓一把，中文圈有小傅哥（bugstack）的《AI MCP Gateway 网关服务系统》全套教程，还有 B 站"Spring AI + MCP + RAG 校招项目"视频。**面试官说"模板"，多半不是因为代码差，而是因为"这题我见过太多次了"。**
3. **一句话定位：项目骨架是 A 级，故事是 C 级，包装是 B− 级。** 代码质量撑得起"特别好"的评价；缺少独特点 + 仓库外观有 AI 生成痕迹，又撑得起"就是模板"的评价。**两者都对，评价差异取决于对方见过多少同类项目。**

**模拟打分（技术面试官视角，满分 10）：**

| 维度 | 得分 | 一句话点评 |
|---|---|---|
| 代码能力 | 7.0 | 分层清晰、异步规范、抽象到位，应届里偏上 |
| 系统设计 | 6.0 | 有分层意识，缺分布式/多租户/安全纵深视角 |
| 工程化闭环 | 7.5 | 测试/CI/Docker/指标齐全，这一项最扎实 |
| 岗位匹配度（AI 应用工程师） | 5.5 | 缺 LLM 集成、缺 Agent 示例、缺流式——"AI"含量不足 |
| 独特记忆点 | 6.0 | inprocess 改造故事好，但整体无差异化记忆点 |

---

## 二、同类项目调研（GitHub / 开源生态 / 中文教程圈）

### 2.1 直接同类：GitHub 上的 MCP 网关（一抓一把）

| 项目 | 形态 | 功能要点（据仓库简介） | 与本项目差异 |
|---|---|---|---|
| [aiguicai/MCP-Gateway](https://github.com/aiguicai/MCP-Gateway) | 网关 | 多 MCP Server 与 Skill 统一接入、**代理转发**、认证、管理 API；把本地 stdio 暴露为远程 MCP 端点 | 多"转发 + 管理 API"视角，双语文档 |
| [jonfairbanks/mcp-gateway](https://github.com/jonfairbanks/mcp-gateway) | 网关 | 路由、**缓存**、监控、访问控制 | 多了缓存层 |
| [MCPJungle](https://github.com/mcpjungle/mcpjungle) | 管理平台 | "一个地方管理连接所有 MCP Server"，**带 Web UI** | 有可视化面板 |
| [Miguell-J/mcp-one](https://github.com/Miguell-J/mcp-one) | 网关 | 中央服务器连接多个 MCP Server，向客户端暴露统一接口 | 概念几乎一致 |
| [hah23255/mcp-context-forge](https://github.com/hah23255/mcp-context-forge) | 网关 | **REST API → MCP 转换**、虚拟 MCP Server 组合、stdio/SSE 协议互转、安全与可观测 | 多了"协议转换"卖点 |
| [roddutra/agent-mcp-gateway](https://github.com/roddutra/agent-mcp-gateway) | 网关 | **按需只加载 3 个工具而非全量注入**（防上下文膨胀）、per-agent 访问控制、支持 OAuth 动态注册 | **语义工具筛选**——正是本项目缺失的差异化方向 |
| [tesfayealex/mcp-agent-gateway](https://github.com/tesfayealex/mcp-agent-gateway) | 网关 | mcp_proxy_server | 同类 |
| [sitbon/magg](https://github.com/sitbon/magg) | 网关 | 被 awesome 清单收录 | 同类 |
| [tanvir-ux/agentic-mcp-gateway](https://github.com/tanvir-ux/agentic-mcp-gateway) | 作品集项目 | "Production-style MCP server + agentic RAG + tool calling，**portfolio demo**" | 连海外应届生作品集都知道要挂 "production-style" 标签 |
| npm：[mcp-gateway](https://www.npmjs.com/package/@ildunari/mcp-gateway)、[mcp-hub-lite](https://www.npmjs.com/package/@loop_ouroboros/mcp-hub-lite) | 网关 | Node 生态同类 | 生态拥挤的又一证据 |
| [e2b-dev/awesome-mcp-gateways](https://agentskillshub.top/skill/e2b-dev/awesome-mcp-gateways/) | 收录清单 | 专门有人维护"awesome-mcp-gateways"安全等级/质量评分清单 | **赛道已卷到有专门榜单** |

### 2.2 上位替代：商业/社区级 AI 网关（面试官会拿来对比）

- [LiteLLM](https://github.com/Das-rebel/awesome-llm-gateways)、One-API、OpenRouter：LLM 统一入口 + 模型路由 + 计费，是"网关"品类的天花板参照物
- [Higress / Apache APISIX / Kong AI Gateway](https://www.apiseven.com/ai-gateway-comparison)：API 网关厂商全面切入 AI Gateway，鉴权/流控/审计/模型路由都是标配
- [火山引擎《企业级 LLM Gateway 六大核心能力》](https://developer.volcengine.com/articles/7670117360598974515)：鉴权、流控、审计、模型路由、可观测、安全——**这六项就是面试官心里"真网关"的 checklist**，本项目目前只覆盖了其中两项（鉴权、流控的基础版）

### 2.3 中文教程圈（"模板"印象的真正来源）

- [小傅哥（bugstack）《AI MCP Gateway 网关服务系统》](https://origin.bugstack.cn/md/project/ai-mcp-gateway/notes.html)：从工程初始化到[部署云服务器](https://origin.bugstack.cn/md/project/ai-mcp-gateway/%E7%AC%AC4-1%E8%8A%82%EF%BC%9A%E6%8A%8A%E7%BD%91%E5%85%B3%E9%83%A8%E7%BD%B2%E5%88%B0%E4%BA%91%E6%9C%8D%E5%8A%A1%E5%99%A8.html)的全套教程，还专门写了"[AI MCP 网关，可以写简历啦！](https://origin.bugstack.cn/md/project/ai-mcp-gateway/promotion/ai-mcp-gateway-stage-completion.html)"
- [B 站校招 Java 项目：Spring AI + MCP + RAG 全流程实战](https://www.bilibili.com/video/BV1VYbczDER6/)

**结论：当面试官说"这项目像 AI 生成的模板"时，他脑内对照的很可能就是上面这些项目。** 功能面（聚合 + 鉴权 + 限流 + 日志 + 指标）在 2026 年已经是"网关标配"，不再构成亮点；亮点必须来自**别人没有的**那一个点。

---

## 三、HR 视角点评（不懂技术，但懂关键词与"像不像样"）

### 3.1 加分项（HR 一眼能抓到的）

| 项 | 现状 | 说明 |
|---|---|---|
| 关键词覆盖 | 较好 | Python / FastAPI / MCP / Docker / CI / 异步 / 鉴权 / 限流 / 监控指标，都是当前热门词 |
| 可量化成果 | 有 | 63 个测试、GitHub Actions 全绿、双容器部署、Vercel 在线 demo（链接待补进 README） |
| 结构化思维 | 有 | docs 里躺着完整的规划/进度/部署三份文档，应届生里少见 |

### 3.2 减分项（HR 看得出来的"不像样"细节——不改必扣分）

| # | 问题 | 影响 | 处理 |
|---|---|---|---|
| 1 | **仓库名带 `Demo`**（`MCP_Gateway_Demo`） | 第一印象就是"练习项目"，HR 与面试官都减分 | 改名 `mcp-gateway` 或 `agent-gateway`（GitHub 仓库改名 + 本地 remote 更新） |
| 2 | **README 没有 demo 链接**（Vercel 链接只写在 docs 里） | 面试官无法一键体验，这是最大的流量浪费 | README 顶部加"在线体验"按钮 + 截图 + 录屏 |
| 3 | **README 结构图里有 `examples/`，目录实际不存在** | 图文不符，一眼看出"没做完" | 补上 examples/（见 P0-1）或删掉该行 |
| 4 | **docs 里躺着 `新建 文本文档.txt`**（一份 AI 提示词草稿，且未被 git 跟踪） | 仓库卫生差，是"AI 生成痕迹"最直观的证据 | 删除或移出仓库；同理检查 `.opencode/` 是否要保留（见 3.4） |
| 5 | **零 star、零 PR、零社区输出** | 无法证明影响力；阶段 5（推广/PR）完全空白 | 见 P1/P2 与"开源运营"建议 |
| 6 | **全中文 README** | 国内企业可接受；外企/大厂国际化团队直接扣分 | 补英文 README（P2） |

### 3.3 简历关键词对照表（HR 会搜什么 vs 你写了什么）

| HR 搜索词（AI 应用工程师） | 当前项目覆盖 | 缺口 |
|---|---|---|
| Python / FastAPI / 后端开发 | ✅ 强覆盖 | — |
| **MCP / 智能体 / Agent** | ✅ 强覆盖 | — |
| **RAG / 向量检索 / Embedding** | ❌ 无 | 简历其他经历（毕设/海科项目）有，但**这个项目里没有代码支撑** |
| **LangChain / 大模型应用** | ❌ 无 | examples/ 缺失，项目零 LLM 集成代码 |
| **流式输出 / SSE** | ❌ 无 | 网关只做同步 JSON 返回 |
| **多租户 / RBAC / 权限** | ❌ 无 | README 里只写了"拓展路径" |
| 限流 / 鉴权 / 安全 | ✅ 有（自实现版） | 深度不够（见面试官视角 3.2） |
| Docker / CI/CD / 部署 | ✅ 有 | 缺公网稳定 demo 入口 |

**HR 结论：项目关键词能过初筛，但"AI 应用工程师"最核心的几个词（Agent 示例、LLM 集成、RAG/向量、流式）一个都没出现在这个仓库里。** 简历上写"熟悉大模型应用开发"，面试官点进仓库却发现没有任何 LLM 调用代码——这是最大的错位。

### 3.4 关于"AI 生成痕迹"的定位建议（辩证看）

仓库里的 `AGENTS.md`、`.opencode/skills/`、`opencode.json` 是 AI 辅助开发工具链的产物。对 2026 年的"AI 应用工程师"岗位，**"熟练使用 AI 辅助开发工具链"本身可以是卖点**，但需要你主动讲出来（"我用 MCP + 本地 Agent 工具链管理了这个项目，这是我的 workflow"），而不是让面试官自己从文件里猜。**`新建 文本文档.txt` 这类纯草稿必须清掉**——那不是工具链，是没收拾的桌面。

---

## 四、技术面试官视角点评（只看能力与岗位匹配度）

### 4.1 真实亮点（面试官会认可、且你能讲出细节的）

1. **三层抽象做得好**：`APIKeyStore` 用 Protocol 抽象（SQLite 实现可无痛换 Redis/PostgreSQL）、`create_client()` 工厂按 transport 选客户端、registry 对上层屏蔽客户端差异——**"面向接口不面向实现"是真懂，不是背概念**。
2. **真踩坑、真解决，故事性最强的三件事**：
   - mcp 2.0.0 的 `streamable_http_client` 从 3 元组 yield 变 2 元组，潜伏到 docker 冒烟才暴露（阶段 2 只测了 stdio）→ 补了 http 集成测试。**这个故事能讲"为什么集成测试重要"**。
   - Vercel 子进程找不到 `mcp` 依赖（Serverless 解释器环境不可控）→ 自研 **inprocess 传输**，进程内直接持 `MCPServer` 实例。**这个故事能讲"部署环境倒逼架构演进"**——这是整个项目最值钱的一段经历。
   - `*.vercel.app` 大陆 DNS 污染 → 用 GitHub Runner 的 `demo-health` workflow 做验收。**这个故事能讲"工程闭环思维"**。
3. **异步基本功扎实**：`asyncio.gather` 并发连接且单点失败不阻塞整体、`asyncio.to_thread` 包 sqlite、`AsyncExitStack` 管理连接生命周期——每一条都能被追问且答得上。
4. **工程闭环完整**：63 测试（含独立子进程集成测试）、ruff 双检、CI、Docker 双容器、Prometheus 自定义指标 + `ToolCallTimer` 上下文管理器——**这套东西很多工作 1–2 年的人都没有**。
5. **安全直觉有雏形**：SQL 只读校验（单语句 + 词边界正则）、`is_error` 与异常区分记录、429 带 `Retry-After`——说明想过"这东西上线会出事"。

### 4.2 会被追问的技术点（每一条都是送分题或送命题，附应对话术）

| # | 技术点 | 面试官会怎么问 | 现在的弱点 | 应对话术 / 改进方向 |
|---|---|---|---|---|
| 1 | 限流器单进程内存态 | "两个 uvicorn worker 呢？水平扩展呢？" | 每个进程各自计数，限流失效 | 承认 MVP 取舍，讲清升级路径：Redis 令牌桶/滑动窗口（Lua 原子操作） |
| 2 | API Key **明文存 SQLite** | "Key 泄露怎么办？你比 GitHub 的 token 存储差在哪？" | 明文存储，无哈希 | 加盐哈希存储（参考 GitHub token 只存 SHA-256 摘要）+ 前缀展示 + 轮换接口 |
| 3 | 按 Key 的桶字典无上限 | "长期运行内存会怎样？" | 无淘汰机制，内存缓慢增长 | 加 LRU 淘汰 / 定期清理空闲桶（小改动，性价比高） |
| 4 | **README 第一行自称"企业级"** | "哪体现企业级？多租户？RBAC？审计？高可用？" | 名不副实，一戳就破 | 二选一：去掉"企业级"改为"工具网关"；或补最小多租户（见 P1-7） |
| 5 | **无流式** | "AI 应用标配的流式输出你支持吗？" | 同步 JSON 返回，MCP 的流式能力未透传 | 网关加 SSE 透传（`POST /tools/{name}/call/stream`） |
| 6 | **examples/ 缺失，零 LLM 代码** | "你的简历写大模型应用，这个项目里 LLM 在哪？" | NL2SQL 是规则模板，没有任何 LLM 调用 | **P0**：写一个 Agent 示例（见下） |
| 7 | NL2SQL 是规则引擎 | "这不叫 AI 吧？" | 诚实但撑不起"AI"卖点 | 双引擎：规则兜底 + 可选 LLM（配置切换），讲"接口与实现解耦，可平滑替换"的故事（架构上已预留） |
| 8 | 连接失败无重连 | "server 挂了恢复后网关能自愈吗？" | 只在启动时 connect，失败即永久标记 | 加健康检查 + 指数退避重连 |
| 9 | `/metrics` 无鉴权 | "指标接口裸奔，泄露内部结构" | 未保护 | 网关类项目指标一般内部暴露；可在 README 说明或加简单保护 |
| 10 | 超时只有 read_timeout | "一个死循环工具调用会挂多久？" | 无整体 deadline | 加 `asyncio.timeout` 总控 |
| 11 | SSE 传输无测试 | "你声称支持三传输，测试只覆盖了 stdio/http/inprocess" | 测试缺口 | 补 SSE 集成测试（或明说 SSE 为实验支持） |
| 12 | 认证单一、无密钥管理 | "怎么给客户发 Key？能过期吗？" | 只有 YAML 种子写入 | 加 `POST /keys` 管理接口 + 过期时间（P2） |

### 4.3 岗位匹配度分析（关键：你投的是"AI 应用工程师"）

- 这个项目**本质是"AI 基础设施"项目**（网关层），不是"AI 应用"项目。
- 面试官对 AI 应用工程师的默认预期：**调过 LLM、写过 Agent/function-calling、处理过 prompt/上下文/流式**。你的网关证明了你会写后端，但**没证明你会做 AI 应用**。
- 好消息：网关恰好是 AI 应用的地基，补上 examples/ 后故事是连贯的——**"我既懂 Agent 应用（examples），又懂 Agent 的地基（网关）"**，这个组合比只做应用或只做网关都稀缺。
- 你的毕设（Windows MCP Server 二次开发 90+）、海科 NL2SQL、鸿蒙 AI 控制闭环都在简历上，**面试时要把这些经历和网关项目串成一条主线**："我在 MCP 生态里从 server 做到 gateway"。

### 4.4 技术面试官最终评价

> 代码能力在应届生里偏上，工程化意识甚至超过部分初级工程师；但**选题无差异化、AI 应用含量不足、安全与分布式视角欠缺**。如果简历上只有这一个项目，面试官大概率给出"基本功不错，但没看到让我记住你的东西"的评价——**差的就是一个差异点和一段完整的故事线**。

---

## 五、为什么同一个项目评价两极分化

| 说"特别好"的人看到 | 说"模板"的人看到 |
|---|---|
| 分层架构、抽象、异步规范 | "MCP 网关"选题，bugstack 教程同款 |
| 63 测试 + CI + Docker 全绿 | 无 star、无 PR、无社区痕迹 |
| inprocess 改造、Vercel 踩坑 | README 无 demo 链接、无截图 |
| 真实部署与验收流程 | 仓库名带 Demo、docs 里有"新建 文本文档.txt" |
| 规划文档完备 | 阶段 5 空白、examples/ 不存在 |

**本质：前者在评"代码"，后者在评"作品"。** 招聘场景里面试官评的是"作品"——代码只是其中一部分。

---

## 六、优化建议（按优先级，每条含"面试故事"）

> 原则：**每一个改动都必须能回答"面试官问起来，这能讲出什么不一样的故事？"** 讲不出故事的不做；故事小的优先做。已有 `新建 文本文档.txt` 里列了 4 个优化方向（语义工具检索 / 真网关能力 / 可观测性 / README 改造），本清单与其对齐并补充。

### P0 — 不做就白做（预计 3–5 天）

**P0-1 写 `examples/`：Agent 通过网关调用工具的完整示例（AI 应用含量从 0 到 1）**
- 做什么：OpenAI function-calling 风格（或 LangChain）Agent：拿 `/tools` 列表 → LLM 选工具 → `POST /tools/{name}/call` 循环直到完成，跑通"查询总销售额"这类任务；输出 Markdown 演示脚本 + 效果录屏。
- 面试故事："我的网关不是玩具，是一个真 Agent 在背后通过它干活；我既写网关又写消费网关的 Agent。"
- 验收：`uv run python examples/agent_demo.py` 一键演示。

**P0-2 仓库外观大扫除（半天）**
- 删除 `docs/新建 文本文档.txt`（及任何同类草稿）；仓库改名去掉 Demo（`mcp-gateway`），同步本地 remote；git status 干净。
- 面试故事：无需故事，这是"不扣分"的必要项。

**P0-3 README 改造：把"成果"放到第一屏（1 天）**
- 顶部加：在线体验链接（Vercel，注明大陆访问限制）+ 演示截图/录屏 + "5 秒看懂"动图。
- 新增"设计决策与踩坑（ADR）"节：inprocess 传输为何诞生、mcp 2.0.0 传输签名变更、vercel.app DNS 污染与验收方案——**把 4.1 的三段故事固化成文档**。
- 修正结构图（examples/ 补齐后自然解决图文不符）。
- 面试故事：README 是面试官打开仓库的第一屏，ADR 节让他 30 秒内知道你经历过什么。

### P1 — 做出差异化（预计 1–2 周，按性价比排序）

**P1-1 语义工具检索（对齐 `新建 文本文档.txt` 的 P0 判定，也是 roddutra/agent-mcp-gateway 的卖点）**
- 做什么：工具 > N 个时，按 query embedding 只注入 top-k 进 Agent 上下文（可先用本地 embedding 或 LLM 打分，不引入重依赖）；`GET /tools?query=...` 支持相似度排序。
- 面试故事："网关的价值不只是聚合，而是**在 Agent 上下文窗口里做工具路由**——工具多了全量注入会爆 token，这是网关层的经典问题。" 这句话直接对打"模板"质疑。
- 验收：README 放一个"20 个工具只注入 5 个"的对比数据。

**P1-2 NL2SQL 双引擎：规则兜底 + 可选 LLM**
- 做什么：`nl2sql.py` 已有"引擎与工具层解耦"的设计，补一个 `LLMTranslator`（走 `examples/` 同款 LLM 客户端），YAML 开关切换；规则不命中时降级 LLM。
- 面试故事："同一个接口，规则引擎和 LLM 实现可以互换——我提前把解耦做好了，换实现零改动。"（架构意识 + AI 应用能力双展示）

**P1-3 流式转发**
- 做什么：`POST /tools/{name}/call/stream`，SSE 逐段转发 MCP 的流式内容。
- 面试故事："AI 应用体验的命门是流式，网关必须透传而不只是包一层 JSON。"（回应 4.2-5）

**P1-4 最小多租户（回应"企业级"质疑的最短路径）**
- 做什么：Key 增加 `tenant` 字段 + 工具白名单（每个 tenant 只能调授权的工具），`/servers` 与鉴权链路上加过滤。
- 面试故事："我从单租户网关做成了多租户网关，权限模型是这周加的"——README"企业级拓展路径"第一项落地，名实相符。

### P2 — 加分项（有余力再做）

- 密钥哈希存储 + 前缀展示 + Key 管理接口（回应 4.2-2/12）
- 限流器 Redis 实现 + 桶 LRU 淘汰（回应 4.2-1/3）
- 连接健康检查 + 指数退避重连（回应 4.2-8）
- OpenTelemetry 链路追踪（与 `metrics.py` 同层，成本可控）
- 英文 README
- 压测数据：wrk/locust 跑一份"网关 vs 直连"的延迟/吞吐对比，写进 README（**量化数据是简历里最值钱的东西**）

### 不建议做（成本高、差异化低）

- 自研 Web 管理面板（MCPJungle 已有，且工作量巨大，对面试无边际收益）
- 堆更多传输协议（三传输 + inprocess 已足够讲故事）
- 做模型路由/计费（One-API 已做到天花板，你追不上也不该追）

### 开源运营（阶段 5 的务实版）

- 不必追求 star 数量；**目标改为"可被搜索、可被引用"**：仓库改名、README 加英文摘要、打 `mcp` `gateway` `fastapi` topics、发一篇掘金/知乎技术文（把 inprocess 踩坑写成《在 Serverless 上跑 MCP 网关的三种姿势》这类标题，附仓库链接）。
- 向 MCP 生态提 PR 保持原计划（哪怕合不进去，提 PR 的过程和记录也是面试谈资）。

---

## 七、简历改写建议（供参考，写入简历前再打磨）

**改前（现在）：**
> 开源项目「MCP 智能体网关」：基于 FastAPI 的统一 MCP 工具网关，支持 stdio/SSE/HTTP 三种传输，聚合多个 MCP Server 供上层 Agent 统一调用；自实现 API Key 鉴权、令牌桶限流与 Prometheus 指标。

**改后（建议方向）：**
> 开源项目「MCP 智能体网关」（GitHub 链接）：基于 FastAPI + asyncio 的多 MCP Server 统一接入网关，支持 stdio/SSE/Streamable HTTP/进程内四种传输；自实现 API Key 鉴权与令牌桶限流（含 429 重试语义），63 项测试 + GitHub Actions CI + Docker 双容器部署全绿。
> - 为 Serverless 环境自研进程内 MCP 客户端，解决子进程依赖缺失问题（部署环境倒逼架构演进）
> - 编写 LangChain/Function-Calling Agent 示例，经网关完成"自然语言→SQL→结果"全链路（可在线体验，附链接）
> - 网关层做语义工具路由，>N 个工具时按查询相似度只注入 top-k，控制 Agent 上下文 token 预算

（注：后两条需先完成 P0-1 / P1-1 再写进简历，**简历上的每一句话都必须能在仓库里找到证据**。）

---

## 八、下一步行动清单（等用户确认后执行）

| 天 | 事项 | 对应 |
|---|---|---|
| Day 1 | 仓库大扫除：删草稿、改名、git 状态清零 | P0-2 |
| Day 1–2 | README 改造：demo 链接 + 截图 + ADR 踩坑节 | P0-3 |
| Day 2–4 | `examples/` Agent 示例 + 录屏 | P0-1 |
| Day 5–8 | 语义工具检索 或 NL2SQL 双引擎（二选一先做） | P1-1 / P1-2 |
| Day 9–12 | 流式转发 + 最小多租户 | P1-3 / P1-4 |
| Day 13–14 | 安全加固（哈希存储 + 桶淘汰）＋ 压测数据 | P2 精选 |
| 机动 | 掘金/知乎发文 + GitHub PR 尝试 | 开源运营 |

---

## 附：参考来源

- 同类项目：https://github.com/aiguicai/MCP-Gateway ｜ https://github.com/jonfairbanks/mcp-gateway ｜ https://github.com/mcpjungle/mcpjungle ｜ https://github.com/Miguell-J/mcp-one ｜ https://github.com/hah23255/mcp-context-forge ｜ https://github.com/roddutra/agent-mcp-gateway ｜ https://github.com/tesfayealex/mcp-agent-gateway ｜ https://github.com/sitbon/magg ｜ https://github.com/tanvir-ux/agentic-mcp-gateway ｜ https://www.npmjs.com/package/@ildunari/mcp-gateway ｜ https://www.npmjs.com/package/@loop_ouroboros/mcp-hub-lite ｜ https://agentskillshub.top/skill/e2b-dev/awesome-mcp-gateways/
- 商业级 AI 网关：https://www.apiseven.com/ai-gateway-comparison ｜ https://api7.ai/litellm-vs-truefoundry ｜ https://github.com/Das-rebel/awesome-llm-gateways ｜ https://developer.volcengine.com/articles/7670117360598974515
- 中文教程圈：https://origin.bugstack.cn/md/project/ai-mcp-gateway/notes.html ｜ https://origin.bugstack.cn/md/project/ai-mcp-gateway/promotion/ai-mcp-gateway-stage-completion.html ｜ https://www.bilibili.com/video/BV1VYbczDER6/
