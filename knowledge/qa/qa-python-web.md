---
id: qa-python-web
type: qa
title: "技术问答 — asyncio / SSE / FastAPI / 接口设计"
tags: [asyncio, 事件循环, 协程, gather, to_thread, SSE, WebSocket, Server-Sent Events, 流式, FastAPI, 异步, 接口设计, RESTful, 状态码, 统一响应]
related: [project-mcp-gateway, qa-storage-infra]
updated: 2026-09-14
---

# 技术问答 — asyncio / SSE / FastAPI / 接口设计

> 来源：速答手册 §6 策展转化。

## asyncio 是什么、什么时候用？

- 事件循环 + 协程，适合 IO 密集场景；gather 并发、to_thread 包同步阻塞。
- 延伸（结合本人工作）：网关里 SQLite 就是 to_thread 包的，防止卡事件循环（见 project-mcp-gateway）。

## SSE 和 WebSocket 怎么选？

- SSE 基于 HTTP 单向服务器推，轻量够用（我的流式输出就是 SSE）；WS 双向，适合聊天室类。
- 延伸（结合本人工作）：网关 Agent 流式 `/agent/run/stream`（step → token* → done）与工具调用流式两条 SSE 通道；鸿蒙项目的传感器实时监控用的是 WebSocket（双向场景）——两个都实际用过，按场景选。

## FastAPI 的优点？

- 原生异步、类型注解即校验、自动 OpenAPI 文档、依赖注入。

## 接口设计的习惯？

- 统一响应结构、状态码语义正确。
- 延伸（结合本人工作）：网关里 401/403/404/429/502 各有明确含义——401 未授权、403 白名单拒绝、404 工具不存在（与 403 区分避免泄露存在性）、429 限流带 Retry-After、502 下游调用超时（见 project-mcp-gateway）。
