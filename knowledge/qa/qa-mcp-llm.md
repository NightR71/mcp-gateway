---
id: qa-mcp-llm
type: qa
title: "技术问答 — MCP / RAG / 微调 / NL2SQL 安全"
tags: [MCP, Model Context Protocol, RAG, 检索增强, 向量库, embedding, LoRA, QLoRA, 微调, NL2SQL, Text2SQL, SQL安全, 只读校验, 注入拦截, 大模型]
related: [project-mcp-gateway, project-harmonyos-smart-home, project-ocean-nl2sql, evidence-main-story]
updated: 2026-09-14
---

# 技术问答 — MCP / RAG / 微调 / NL2SQL 安全

> 来源：速答手册 §6 策展转化（要点为口语口径，模型按人设转述；不得超出来源添加主张）。

## MCP 是什么？

- Model Context Protocol，Anthropic 开源的模型上下文协议，统一 LLM 和外部工具/资源的连接标准；Server 提供工具，Host/Client 调用。类比 AI 应用的 USB-C。
- 延伸（结合本人工作）：我的网关解决的是 MCP 生态里"多 Server 怎么统一管理"的问题（见 project-mcp-gateway）。

## RAG 流程？

- 文档切分 → embedding 入向量库 → 检索 top-k → 拼 Prompt → 生成。
- 痛点是切分策略和检索质量。
- 延伸（如实）：我的网关语义路由是同思路的轻量替代——关键词 2-gram 加权打分替代向量检索，零依赖零网络，Scorer 接口预留 embedding 升级。

## LoRA 原理？

- 冻结原权重，旁路训低秩矩阵，训练参数少几个量级；配 4bit 量化就是 QLoRA，消费级显卡能跑。
- 延伸（结合本人工作）：鸿蒙项目实测 r=16、alpha=16、7 个投影矩阵（见 project-harmonyos-smart-home）。
- 微调效果被追问 → 按鸿蒙卡 L3 诚实口径（450+ 条、部分场景不稳定、解析兜底、无验证集），不在这里展开新主张。

## NL2SQL 安全怎么做？

- 只读校验：仅 SELECT/WITH、危险关键字拦截、分页截断、白名单表；
- 护栏：违规词检测 / 注入检测（口述级经历见 project-ocean-nl2sql）；
- 延伸（结合本人工作）：网关 demo_sql 的只读校验（仅 SELECT/WITH、危险关键字拦截、50 行截断）在工具层强制执行（见 project-mcp-gateway）。
