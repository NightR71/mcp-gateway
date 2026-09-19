---
id: qa-storage-infra
type: qa
title: "技术问答 — MySQL/PostgreSQL/Redis/缓存一致性/限流/批量导入"
tags: [MySQL, 索引, B+树, 最左前缀, 覆盖索引, PostgreSQL, Redis, 缓存一致性, cache-aside, 延迟双删, binlog, 慢查询, explain, 令牌桶, 限流, 分布式限流, Lua, Excel, 批量导入, 分批事务, 数据一致性, 对账]
related: [project-mcp-gateway, project-internship-medical-saas, qa-python-web]
updated: 2026-09-14
---

# 技术问答 — MySQL/PostgreSQL/Redis/缓存一致性/限流/批量导入

> 来源：速答手册 §6 + §10.3 策展转化。

## MySQL 索引？

- B+ 树；最左前缀、覆盖索引避回表；对字段做函数/隐式转换会失效。

## PostgreSQL 了解吗？

- 了解并在学，SQL 标准和事务模型与 MySQL 相近；我的项目存储层做了 Protocol 抽象，换 PG 只换实现层、业务零改动——这正是抽象的价值。
- 延伸（结合本人工作）：网关 Key 存储是 SQLite + `APIKeyStore` Protocol（可换 Redis/PostgreSQL）；实习一的系统用 PostgreSQL（MyBatis-Plus）。

## Redis 与分布式限流？

- 我的令牌桶限流是单机内存版（按 Key 维度，429 带 Retry-After）；分布式方案就是 Redis + Lua 保证原子性；缓存场景用 cache-aside + TTL。
- 延伸（结合本人工作）：访客场景已扩小时桶（容量 50/小时）；"单机内存版"主动承认 + 给出升级路径（见 project-mcp-gateway）。

## 缓存一致性？

- 常规方案先更新库再删缓存 + 过期时间兜底；要求高就延迟双删或订阅 binlog 异步失效。
- 取舍意识（必答点）：按场景选——我项目里的限流状态最终一致就够，不用为强一致过度设计。

## 慢查询怎么排查？

- explain 看执行计划、补索引（最左前缀/覆盖索引）、深分页用游标或子查询改写。

## 业务数据一致性（批量/跨服务）？

- 批量导入用分批事务 + 失败行回执；跨服务用最终一致 + 对账。
- 延伸（结合本人工作）：实习一导入链路是"全量校验 + 单事务落库 + 错误明细回执"，量级适配基层几十到几百条/次——大文件改法（流式/分批）知道怎么做、如实说未实现（见 project-internship-medical-saas L3）。

## Excel 批量导入怎么设计？

- 分批读取校验、分批事务提交、失败行回执、异步化 + 进度反馈。
- 延伸（结合本人工作）：实习一的实际落地是键值对解析 + 声明式表头映射 + 全量校验 + 事务落库 + 错误回执下载（见 project-internship-medical-saas）。
