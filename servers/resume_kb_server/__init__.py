"""resume_kb_server：只读简历知识库 MCP Server（M3）。

与 `demo_sql_server` 同模式（YAML 声明式 inprocess 接入），但职责是**只读**：
把 `knowledge/` 目录下的 Markdown 知识卡片（YAML frontmatter + 正文）加载进内存，
提供「按语义检索卡片」的工具集给上层 Agent 使用。

只读保证（对应执行计划 §5.1 第 2 层「工具层只读」）：
- 对外只有查询类工具，没有任何写入/删除/执行类工具；
- 知识文件以只读方式打开，检索结果原样返回卡片内容。
"""
