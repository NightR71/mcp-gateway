"""示例 MCP Server：demo_sql_server。

阶段 2：只暴露一个 echo 工具，用于跑通「网关 → stdio → server」全链路。
阶段 4：升级为自然语言 → SQL 查询（复用作者 NL2SQL 经验，SQLite 落库）。

独立运行方式（供网关以 stdio 传输拉起）：
    python servers/demo_sql_server/server.py
"""

from mcp.server.mcpserver import MCPServer

server = MCPServer("demo_sql_server")


@server.tool()
async def echo(message: str) -> str:
    """回显输入的消息（链路调试用）。"""
    return f"echo: {message}"


if __name__ == "__main__":
    server.run(transport="stdio")
