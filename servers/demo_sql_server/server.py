"""示例 MCP Server：demo_sql_server（自然语言 → SQL 查询）。

阶段 2：echo 工具跑通「网关 → stdio → server」全链路。
阶段 4：升级为迷你电商库的 NL2SQL 演示（规则模板引擎，SQLite 落库）：
- ask         自然语言提问 → 生成 SQL → 执行并返回结果
- run_sql     直接执行只读 SQL（强制 SELECT/WITH 单语句校验）
- list_tables 查看演示库表结构
- echo        链路调试用回显

独立运行方式：
    python servers/demo_sql_server/server.py            # stdio（供网关拉起）
    DEMO_SQL_TRANSPORT=http python .../server.py        # Streamable HTTP（docker 用）

环境变量：
    DEMO_SQL_TRANSPORT  stdio（默认）/ http
    DEMO_SQL_HOST       http 模式监听地址（默认 0.0.0.0）
    DEMO_SQL_PORT       http 模式端口（默认 9001）
    DEMO_SQL_DB_PATH    SQLite 路径（默认 data/demo_sql.db）

注意：本文件按脚本方式运行（sys.path[0] 为本目录），故 db / nl2sql 用裸导入。
"""

import os
from collections.abc import Iterable
from typing import Any

from db import execute_readonly, get_schema, init_db
from mcp.server.mcpserver import MCPServer
from nl2sql import SUPPORTED_EXAMPLES, question_to_sql

DB_PATH = os.getenv("DEMO_SQL_DB_PATH", "data/demo_sql.db")

server = MCPServer("demo_sql_server")

_db_ready = False


def _ensure_db() -> None:
    """首次调用时建库 + 种子（惰性初始化，避免 import 副作用）。"""
    global _db_ready
    if not _db_ready:
        init_db(DB_PATH)
        _db_ready = True


def _to_markdown_table(columns: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    """把查询结果渲染为 Markdown 表格文本。"""
    columns = list(columns)
    rows = [tuple(row) for row in rows]
    if not rows:
        return "（查询成功，无结果行）"
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(str(value) for value in row) + " |" for row in rows]
    return "\n".join([header, separator, *body])


@server.tool()
async def echo(message: str) -> str:
    """回显输入的消息（链路调试用）。"""
    return f"echo: {message}"


@server.tool()
async def ask(question: str) -> str:
    """用中文自然语言提问，自动生成并执行 SQL 返回结果。

    演示电商库含 customers / products / orders 三表；
    未命中规则时会返回支持的问题示例列表。
    """
    _ensure_db()
    sql = question_to_sql(question)
    if sql is None:
        examples = "\n".join(f"- {e}" for e in SUPPORTED_EXAMPLES)
        return "暂时无法理解这个问题，可以试试这样问：\n" + examples
    columns, rows, truncated = execute_readonly(DB_PATH, sql)
    result = f"生成的 SQL：\n```sql\n{sql}\n```\n\n查询结果：\n" + _to_markdown_table(columns, rows)
    if truncated:
        result += "\n\n（结果行数过多，已截断）"
    return result


@server.tool()
async def run_sql(sql: str) -> str:
    """直接执行只读 SQL（仅允许 SELECT/WITH 单语句），返回 Markdown 表格。"""
    _ensure_db()
    try:
        columns, rows, truncated = execute_readonly(DB_PATH, sql)
    except ValueError as exc:
        return f"SQL 被拒绝：{exc}"
    result = _to_markdown_table(columns, rows)
    if truncated:
        result += "\n\n（结果行数过多，已截断）"
    return result


@server.tool()
async def list_tables() -> str:
    """列出演示库全部业务表的建表语句（表结构说明）。"""
    _ensure_db()
    return "演示库表结构：\n```sql\n" + get_schema(DB_PATH) + "\n```"


if __name__ == "__main__":
    transport = os.getenv("DEMO_SQL_TRANSPORT", "stdio")
    if transport == "http":
        server.run(
            transport="streamable-http",
            host=os.getenv("DEMO_SQL_HOST", "0.0.0.0"),
            port=int(os.getenv("DEMO_SQL_PORT", "9001")),
        )
    else:
        server.run(transport="stdio")
