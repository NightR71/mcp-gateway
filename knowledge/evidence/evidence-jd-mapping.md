---
id: evidence-jd-mapping
type: evidence
title: "JD 匹配映射与 AI 编程方法论"
tags: [JD, 匹配, 岗位匹配, 为什么匹配, AI编程, AI辅助编程, 自然语言式编程, 范式约束, 测试闭环, 经验收敛, 从零到一, 内部业务系统, 数据链路, 前台展示, 数据抽取, Web后端, gap, 应届]
related: [evidence-main-story, project-mcp-gateway, project-internship-medical-saas, project-harmonyos-smart-home, project-ocean-nl2sql]
updated: 2026-09-14
---

# JD 匹配映射与 AI 编程方法论

## JD 三条核心要求 → 我的证据（被问"你觉得你匹配吗"直接用）

1. **内部业务系统 / 数据链路交付** → 实习一：基层公共卫生随访系统（内部业务系统）+ 随访数据批量导入与跨服务字段回填（数据链路）。
2. **熟悉多类真实场景** → 三类全占：前台展示（Agent 工作台 /ui、鸿蒙端）、数据抽取（NL2SQL 数据问答、批量导入）、Web 后端（FastAPI 网关、Spring Cloud 微服务模块）。
3. **从零到一** → MCP Gateway 整个服务（接口/模块/测试/CI/两套部署上线）+ 毕业设计课题 + 随访系统新模块。

## AI 编程方法论（"你们怎么用 AI 编程、怎么保证输出稳定"，JD 高频主考题）

三层做法（60 秒版口径）：

1. **范式约束**——动手前先写约定文档：分层规则、命名规范、公开接口只增不改的兼容红线，让 AI 在框内生成；
2. **测试闭环**——AI 每轮改动必须过全量测试和 lint，项目有 379 项自动化测试（2026-09-20 实测，随跑随更）加 CI 兜底，失败信息直接喂回 AI 修正，测试是裁判，不靠肉眼审代码；
3. **经验收敛**——AI 不稳定的高发区（SDK 版本差异、异常分类），踩过一次就固化成回归测试，同类问题不会再犯。

结果口径：一个人能跑出小团队的交付速度，而且质量是可验证的。

追问"AI 什么时候不可靠"：边界清晰的 CRUD 和模块实现很稳；架构决策、安全边界、第三方 SDK 的坑不可靠——所以约定文档我来写、集成测试我来设计，把 AI 关在框里。

> 数字口径纪律：测试数用实测口径（379 / 2026-09-20，随跑随更）或下限式，不引用旧文档的 156。

## 通用能力映射（任意 JD 的"关键词 → 证据"查表）

| JD 关键词 | 我的证据 | 详见 |
|---|---|---|
| Python / FastAPI / asyncio | MCP Gateway 全栈（FastAPI + asyncio + 全量测试 + CI） | project-mcp-gateway |
| Agent / MCP / 大模型应用 | 网关 + 毕设 + 实习二（口述级）三层经历 | evidence-main-story |
| 微调 / 本地部署 | Unsloth + LoRA 微调 DeepSeek-R1 蒸馏 8B → GGUF → Ollama | project-harmonyos-smart-home |
| RAG / 检索 | 语义路由（关键词加权，同思路轻量实现，Scorer 预留 embedding） | project-mcp-gateway |
| Java / Spring Cloud / 微服务 | 实习一随访系统模块（19 提交 Git 可查） | project-internship-medical-saas |
| MySQL / PostgreSQL / 缓存 | 索引/树形分类设计 + 存储层 Protocol 抽象 + 缓存一致性口径 | qa-storage-infra |
| 测试 / 质量 / CI | 379 项测试（真实子进程集成 + 故障注入 + 评测集回归）+ GitHub Actions | project-mcp-gateway |
| 安全 / 鉴权 / 限流 | API Key + 令牌桶（分钟/小时桶）+ 租户白名单 + 白名单收口 + 错误脱敏 | project-mcp-gateway |

## gap 期口径（2026.01–07 应届空窗）

「毕业和离校事务收尾后，我给自己定了目标：把实习接触的 MCP 生态做成一个完整开源作品再求职，2026.08 上线了这个网关，现在全量测试 + 线上可体验。我认为这比匆忙海投更有价值。」

> 时间口径纪律：只说 **2026.08 开源上线**（Git 时间戳公开，不提前暴露开发窗口）；速答手册旧版 gap 答法里"7-8 月全力做"与红线⑤不一致，已按红线⑤统一（差异已在红线底稿待确认项列出）。
