# MCP Gateway 项目二阶段规划生成器

你现在不是负责直接开发代码。

你的唯一任务是：

> **读取当前项目 docs、真实源码、配置、测试与 Git 状态，结合项目评审文档，重新判断项目当前真实状态，并生成一份新的《项目拓展规划与开发步骤.md》，供后续新的 AI 对话持续开发。**

这次对话的最终产物应该是一份 Markdown 开发规划文档。

不要直接开始大量修改代码。

---

# 一、项目背景

当前项目已经完成第一阶段的基础建设。

项目原有开发交接文档：

```text
docs/MCP-Gateway项目规划与开发步骤.md
```

项目评审文档：

```text
docs/项目评审与优化建议（HR与面试官双视角）.md
```

你必须阅读这两份文档。

其中：

- `MCP-Gateway项目规划与开发步骤.md`：提供项目原始定位、技术架构、开发历史、当前进度、验收结果和已有设计。
- `项目评审与优化建议（HR与面试官双视角）.md`：提供当前项目存在的问题、技术追问、差异化方向和后续优化建议。

但是：

> **文档不能作为唯一事实来源。**

你必须使用当前仓库源码验证文档中的完成状态。

---

# 二、你的任务边界

这次不要直接开发项目。

你的任务是完成：

```text
读取 docs
   ↓
扫描项目源码
   ↓
检查当前真实状态
   ↓
检查测试
   ↓
检查配置 / Docker / examples
   ↓
理解已有架构
   ↓
结合评审意见
   ↓
找出当前项目真正的能力边界
   ↓
重新制定下一阶段开发路线
   ↓
生成新的 Markdown 开发文档
```

最终输出：

```text
docs/MCP-Gateway项目拓展规划与开发步骤.md
```

如果项目已有类似文件名，请根据实际情况选择合理名称。

---

# 三、最重要的要求：不要机械执行原优化清单

你必须重新规划，而不是简单复制：

```text
项目评审与优化建议（HR与面试官双视角）.md
```

里面的：

```text
P0
P1
P2
Day 1
Day 2
...
```

不要直接照搬。

因为原评审中的“仓库大扫除、README、PR、博客、社区推广”等内容，目前优先级较低。

当前策略明确调整为：

> **优先扩展项目代码、功能和架构。**

暂时降低以下内容的优先级：

- README 美化
- README 英文版
- 仓库改名
- 删除草稿
- GitHub Topics
- 博客
- 社区推广
- PR
- star
- 简历修改
- 演示录屏
- 其他外围文档整理

这些未来可以做。

但现在不是重点。

---

# 四、重新规划时必须优先考虑的方向

你需要根据**当前真实代码状态**判断，不是强制全部实现。

重点考虑以下几个方向：

## 1. Agent 能力

当前项目不能长期只是：

```text
MCP Gateway
+
REST API
```

需要逐步形成完整：

```text
User
 ↓
Agent
 ↓
Tool Selection
 ↓
Gateway
 ↓
MCP Server
 ↓
Tool Result
 ↓
Agent
 ↓
Final Response
```

检查现有 `examples/` 是否已经实现 Agent。

如果已经实现，不要重复做。

应该思考：

> 下一步如何让 Agent 能力真正成为项目核心组成部分？

---

## 2. Tool Discovery / Tool Routing

重点评估：

```text
工具数量增加
      ↓
所有工具全部发送给 Agent
      ↓
Context 变大
      ↓
Token 消耗增加
      ↓
工具选择质量下降
```

项目是否应该加入：

- Tool Search
- Tool Discovery
- Tool Ranking
- Semantic Tool Routing
- Top-K Tool Selection
- Context Budget

如果当前架构适合，优先考虑这个方向。

---

## 3. NL2SQL 双引擎

评估当前：

```text
Rule-based NL2SQL
```

是否可以逐步演进成：

```text
             NL2SQL
               │
       ┌───────┴───────┐
       ↓               ↓
 Rule Translator   LLM Translator
       │               │
       └───────┬───────┘
               ↓
        SQL Validation
               ↓
          Database
```

重点考虑：

- Rule 是兜底
- LLM 是可选
- 配置可切换
- Translator 与 Tool 解耦
- 不绑定某一家模型

---

## 4. Streaming

评估当前项目是否适合加入：

```text
/tool/{name}/call/stream
```

以及：

```text
Agent
 ↓
Streaming
 ↓
Gateway
 ↓
MCP Server
```

重点判断：

- 当前 MCP SDK 能否支持
- 当前 Gateway 架构是否容易加入
- Streaming 对 Agent UI 是否有价值
- 是否会破坏当前同步调用

---

## 5. Multi-Tenant / Permission

评估：

```text
API Key
 ↓
Tenant
 ↓
Allowed Tools
 ↓
Gateway
```

是否可以加入：

- Tenant
- Tool Permission
- Tool Whitelist
- API Key ↔ Tenant
- Tenant ↔ Rate Limit

但禁止为了显得“企业级”而无意义扩张。

---

## 6. Gateway Reliability

重点检查：

- MCP Server 断线
- reconnect
- health check
- timeout
- retry
- failure isolation
- circuit breaker

然后判断哪个最值得做。

不要全部实现。

---

## 7. Interactive Agent Workbench

这是新的重要要求。

最终项目不能长期依赖：

```text
python xxx.py
```

也不能只有：

```text
FastAPI /docs
```

需要规划一个**简单但真正可交互的 Agent 界面**。

目标类似：

```text
┌──────────────────────────────────────────────┐
│              MCP Agent Workbench             │
├──────────────────────────────────────────────┤
│                                              │
│ User                                         │
│ 查询目前销售额最高的商品                     │
│                                              │
│ Agent                                        │
│ 正在选择工具...                              │
│                                              │
│ Tool                                         │
│ demo_sql__ask                                │
│                                              │
│ Gateway                                      │
│ Authentication ✓                             │
│ Permission ✓                                 │
│ Execution ✓                                  │
│                                              │
│ Result                                       │
│ ……                                           │
│                                              │
├──────────────────────────────────────────────┤
│ 输入问题............................. [发送]  │
└──────────────────────────────────────────────┘
```

界面不需要特别漂亮。

但必须体现：

- User
- Agent
- Tool Calling
- Gateway
- MCP Server
- Tool Result
- Final Answer

如果技术成本合理，可以继续展示：

- Tool
- Latency
- Status
- Streaming
- Tool Routing
- Execution Trace

---

# 五、不要为了复杂而复杂

规划时必须遵循：

> 每一个新增功能都必须有明确的技术原因。

例如：

```text
Tool 数量增加
→ Tool Routing 有必要
```

```text
Agent 需要实时反馈
→ Streaming 有必要
```

```text
多人使用
→ Tenant / Permission 有必要
```

```text
MCP Server 不稳定
→ Health Check / Retry 有必要
```

禁止为了增加开发周期而：

- 无意义拆微服务
- 无意义引入 Redis
- 无意义引入 Kafka
- 无意义引入 Kubernetes
- 无意义做后台管理系统
- 无意义增加大量依赖
- 无意义增加设计模式
- 无意义增加数据库

---

# 六、必须先扫描真实项目

在规划之前，至少检查：

```text
项目根目录
docs/
app/
servers/
examples/
tests/
config/
pyproject.toml
docker-compose.yml
Dockerfile
README.md
.github/
```

同时检查：

```text
git status
git log --oneline
```

如果当前环境允许，也应该检查：

```text
pytest
ruff
docker
```

但不要为了规划任务而花大量时间修复环境。

---

# 七、必须建立“实际完成状态”

你需要区分：

### 文档声称完成

例如：

```text
[x] Tool Registry
```

与：

### 实际代码完成

例如：

```text
app/mcp/registry.py
tests/test_mcp/test_registry.py
```

与：

### 实际验证完成

例如：

```text
pytest xx/xx passed
```

最终必须判断：

```text
规划完成
代码完成
测试完成
真实运行完成
```

四个状态不要混淆。

---

# 八、重点寻找“已经有基础但还没发挥出来”的能力

不要只寻找“缺什么”。

更应该寻找：

> **当前项目已经写了一半，继续扩展就能形成完整能力的模块。**

例如：

```text
已有 Tool Registry
→ Tool Routing

已有 NL2SQL 抽象
→ LLM Translator

已有 Agent
→ Agent Workbench

已有 metrics
→ Execution Trace

已有 MCP Client
→ Multi-server / Health / Retry
```

这类路径优先级通常高于从零创建完全无关的新系统。

---

# 九、输出的新文档必须具备以下结构

最终生成：

```text
# MCP Gateway 项目拓展规划与开发步骤
```

必须至少包含以下章节：

```text
0. 给新对话的一句话开场
1. 当前项目定位
2. 当前真实能力
3. 当前架构
4. 当前已经完成的功能
5. 当前主要技术缺口
6. 二阶段总体目标
7. 二阶段架构演进
8. 分阶段开发路线
9. 每个阶段的具体任务
10. 每个任务的验收标准
11. 推荐开发顺序
12. 暂不处理事项
13. 风险与兼容性要求
14. 测试要求
15. Agent Workbench 规划
16. 当前状态
17. 下一步建议
```

---

# 十、开发阶段必须细到“新对话可以直接执行”

不要写：

```text
阶段 2：实现 Tool Routing
```

必须细成：

```text
### 阶段 X：Semantic Tool Routing

目标：
工具数量增加后降低 Agent 上下文中的无关工具数量。

修改范围：
- app/...
- examples/...
- tests/...

步骤：
1. 定义 ToolDocument
2. 实现 Tool Index
3. 实现 Query -> Candidate Tools
4. 实现 Ranking
5. 增加 Top-K
6. 接入 Agent
7. 增加测试

验收：
- N 个工具下可以过滤
- Top-K 正确
- 无关工具不会注入
- Agent 能完成任务
- 原有 /tools 不受影响
```

也就是说：

> 后续新的低性能本地 AI 只需要阅读这份文档，就应该能够直接执行下一阶段。

---

# 十一、每个阶段必须有独立验收标准

每个阶段至少包含：

```text
代码验收
测试验收
运行验收
兼容性验收
```

例如：

```text
代码：
新增 Tool Router

测试：
pytest tests/test_router

运行：
Agent 实际执行查询

兼容：
原有 /tools 与 /tools/{name}/call 继续工作
```

---

# 十二、必须设计“断点续开发机制”

新的开发文档必须能够支持：

```text
对话 1
开发阶段 1
↓
更新 Current Progress

对话 2
读取文档
↓
继续阶段 1 / 2
↓
更新 Current Progress

对话 3
读取文档
↓
继续阶段 2 / 3
```

因此必须包含：

```text
## 当前进度
```

其中记录：

- 当前阶段
- 已完成阶段
- 正在进行
- 已完成任务
- 测试数量
- 最新验证结果
- 已知问题
- 环境问题
- 下一步

---

# 十三、新对话开场提示词必须内置

新文档最后必须包含一个可以直接复制的：

```text
## 给新对话的一句话开场
```

或者：

```text
## 新对话继续开发提示词
```

要求新的 AI：

1. 读取当前开发文档
2. 检查实际代码
3. 查看当前进度
4. 不重复已有功能
5. 执行下一项任务
6. 测试
7. 更新进度

推荐采用这样的基本思路：

```text
我正在继续开发 MCP Gateway 项目。

请先阅读：
docs/MCP-Gateway项目拓展规划与开发步骤.md

然后检查当前代码与测试。

以文档中的“当前进度”为主要导航，
但以实际代码和测试结果为最终事实。

不要重复已经完成的工作。

找到当前阶段尚未完成的第一项任务，
先分析实现方案，再开始修改代码。

完成后：
1. 运行相关测试
2. 运行完整测试
3. 检查是否破坏已有功能
4. 更新当前进度
5. 告诉我下一步是什么。
```

必须根据最终实际文档结构调整这个提示词。

---

# 十四、二阶段路线不要无限扩张

必须明确：

> 二阶段首先应该实现一个可以完整运行的核心闭环。

推荐优先形成：

```text
Agent
 ↓
Tool Discovery
 ↓
Tool Routing
 ↓
MCP Gateway
 ↓
MCP Server
 ↓
Tool Result
 ↓
Agent
 ↓
Interactive UI
```

完成以后，再考虑：

```text
Streaming
Multi-Tenant
Permission
LLM NL2SQL
Reliability
Tracing
Performance
```

不要在项目尚未形成完整 Agent 产品闭环之前，同时铺开十几个系统。

---

# 十五、规划结果必须适合“低性能本地 Coding AI”

这是非常重要的约束。

你生成的开发文档不是给架构团队看的。

它是给一个：

> **能力一般、上下文成本有限、需要通过文档恢复项目上下文的本地 AI Coding Agent**

使用的。

因此：

### 不要

```text
实现高级 Tool Routing 架构。
```

### 要

```text
1. 创建 app/mcp/tool_router.py
2. 定义 ToolCandidate
3. 实现 build_index()
4. 实现 search(query, top_k)
5. 给每个工具生成 searchable_text
6. 先使用轻量本地相似度方案
7. 不改变 registry 对外接口
8. 增加 5 个测试
9. 接入 Agent
10. 验收自然语言查询能选择正确工具
```

每个任务尽可能具体。

---

# 十六、禁止在新规划中重复无价值历史信息

新开发文档不是项目百科全书。

可以保留：

- 当前架构
- 关键设计决策
- 已完成核心模块
- 当前阶段
- 已知问题
- 下一步路线

可以删除或压缩：

- HR 评价
- 面试话术
- 简历文案
- 社区宣传计划
- 重复的项目背景
- 已经没有意义的历史过程

---

# 十七、注意保持项目已有设计

新规划不能凭空推翻已有设计。

尤其需要检查并尽量复用：

- MCP Client
- Transport 抽象
- Registry
- API
- API Key
- Rate Limit
- Metrics
- NL2SQL
- Agent examples
- Docker
- Tests
- InProcess Client

只有确认现有设计不适合扩展时，才提出重构。

---

# 十八、规划文档必须给出“为什么这样开发”

每个重要阶段至少说明：

```text
为什么现在做
为什么依赖前面的模块
解决什么问题
完成后项目能力发生什么变化
```

但不要写 HR / 面试话术。

描述应该站在：

> **项目研发、学习探索、系统演进**

的角度。

---

# 十九、最终输出质量要求

你最终生成的不是“建议”。

而是一份：

> **真正可以作为未来数次 Coding Agent 对话的项目开发操作手册。**

要求：

- 可以直接执行
- 有阶段划分
- 有文件级任务
- 有验收标准
- 有依赖关系
- 有优先级
- 有当前状态
- 有新对话接续提示词
- 尽量减少后续 AI 重新理解项目所需要的 Token

---

# 二十、最终动作

现在不要直接开始修改代码。

严格按照下面顺序执行：

```text
Step 1
读取：
docs/MCP-Gateway项目规划与开发步骤.md

Step 2
读取：
docs/项目评审与优化建议（HR与面试官双视角）.md

Step 3
扫描当前仓库结构

Step 4
检查核心代码

Step 5
检查 examples

Step 6
检查 tests

Step 7
检查 Docker / config / pyproject

Step 8
检查 git 状态和最近提交

Step 9
判断文档状态与代码状态是否一致

Step 10
总结当前真实项目能力

Step 11
结合评审意见重新设计二阶段路线

Step 12
输出新的开发规划文档：

docs/MCP-Gateway项目拓展规划与开发步骤.md

Step 13
展示：
- 当前真实状态
- 二阶段核心目标
- 为什么这样规划
- 新文档路径
- 第一阶段是什么
```

**不要在这个对话里直接开始大规模写业务代码。**

你的最终产物必须是：

```text
一个新的、高质量、可以长期交接给后续 AI Coding Agent 使用的 Markdown 开发规划文档。
```