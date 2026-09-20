# knowledge/ — 简历助手知识库（M2 产物，待用户逐条签字）

> 状态：**底稿，签字前不得接入 M3**（执行计划 §0：M2 知识库内容与脱敏终稿 → 用户逐条签字后才能进 M3）。
> 证据底稿：`docs/证据采集/` 三份项目证据文件（2026-09-14 用户确认）。本目录全部事实性内容以其实测口径为准；简历数字一律不引用。
> 安全红线：隐私内容零入知识库（结构性兜底）；产物无第三方真实姓名/邮箱/电话/口令/内网 IP/雇主内部信息（含内部表名）。

## 目录结构

| 文件 | type | 内容 |
|---|---|---|
| `profile/profile-basic.md` | profile | 脱敏基本信息卡 |
| `project/project-mcp-gateway.md` | project | MCP Gateway 智能体工具网关 |
| `project/project-graduation-desktop-agent.md` | project | 毕设：桌面悬浮窗任务 Agent |
| `project/project-harmonyos-smart-home.md` | project | 鸿蒙智能家居 APP 系统 |
| `project/project-ocean-nl2sql.md` | project | 实习二：海洋数据 NL2SQL 专项（口述级、素材待补） |
| `project/project-internship-medical-saas.md` | project | 实习A：基层医疗公共卫生微服务 |
| `qa/qa-mcp-llm.md` | qa | MCP / RAG / LoRA / NL2SQL 安全 |
| `qa/qa-python-web.md` | qa | asyncio / SSE / FastAPI / 接口设计 |
| `qa/qa-storage-infra.md` | qa | 索引 / 限流 / 缓存一致性 / 批量导入 / PG·Redis |
| `qa/qa-hr.md` | qa | HR 高频题（手册 §5 策展；薪资数字结构性不入库） |
| `evidence/evidence-main-story.md` | evidence | 主线故事 + 预设引导顺序 |
| `evidence/evidence-jd-mapping.md` | evidence | JD 匹配映射 |

## Frontmatter 约定（M3 只读加载）

```yaml
---
id: project-mcp-gateway     # 全局唯一，引用键
type: project               # profile | project | qa | evidence
title: "显示标题"
tags: [召回关键词, 同义说法, 面试官问法]   # 语义路由按中文 2-gram / 英文数字切分命中
related: [其他卡片 id]
updated: 2026-09-14         # 实测数字随跑随更时同步改
---
```

召回机制对齐 `app/mcp/tool_router.py`：查询与卡片文本按中文 2-gram 切分（连续中文词天然可命中，无需拆字）、英文/数字按 `[a-zA-Z0-9_]+` 切分。因此 `tags` 写自然中文词与别称即可；跨卡同义说法（如「毕设」「毕业设计」「桌面智能体」）在同卡 tags 中并列，防漏召回。

## 口径规则（全目录强制）

1. **实测优先**：数字一律用 `docs/证据采集/` 实测值，并带实测日期；网关测试数随跑随更（2026-09-20 实测 379 通过 / 1 跳过）。简历/速答手册里的旧数字（156 测试、20 接口、500 条等）一律不引用。
2. **"推断非事实"标注原样沿用**：时间区间等推断性结论在卡内保留「推断」标注。
3. **归属分级原样沿用**：毕设 A/B/C/D 分级、鸿蒙 A 级 + [待确认]、实习A A/B/C 级及"不冒领边界"，表述时按证据文件口径，不升级。
4. **口述级内容显式标注**：实习二（海洋数据）全部主张、实习一团队规模等口述级条目，卡内标注「口述级」，不得写成已验证事实。
5. **诚实边界内嵌**：每张 project 卡的「边界与不作主张」节来自证据文件 G 节，属卡片正文一部分（人设依此拒答/纠偏），不是附注。
6. **改卡片或话术后先跑评测集回归**：65 问面试官高频问题已结构化为 `tests/fixtures/eval_questions.yaml`，三类零成本断言（召回命中 / 红线固定话术命中 / 正常提问不被误拦）随 CI 跑——改知识卡 tags、正文或 `config/resume_kb.yaml` 的红线词表后，跑 `uv run pytest tests/test_m7_eval_set.py` 即可知道哪几问被改坏了，不必人工逐条抽查。

## 追问预案三层定义

- **L1（确认层）**：面试官听完概述的常规追问，考基本事实与动机。
- **L2（深挖层）**：技术实现细节追问，考"是不是真做过"。
- **L3（挑战层）**：质疑、边界、最难问题，考诚实与工程判断。L3 要点必须落在本卡「边界与不作主张」内，禁止超纲兜深度。
