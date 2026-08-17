"""demo_sql_server 包：示例 MCP Server（自然语言 → SQL 查询）。

server.py 以脚本方式独立运行（由网关 stdio 拉起或 docker 以 http 模式启动）；
db / nl2sql 为纯逻辑模块，供 server 调用与单元测试直接导入。
"""
