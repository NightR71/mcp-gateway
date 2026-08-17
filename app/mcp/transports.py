"""MCP 三种传输（stdio / SSE / Streamable HTTP）的连接管理。

把「按配置建连接」这件事集中在这里，client.py 只面向统一的流对。
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from mcp import StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from app.config import MCPServerConfig


@asynccontextmanager
async def create_transport(
    config: MCPServerConfig,
) -> AsyncGenerator[tuple[Any, Any], None]:
    """按 server 配置建立传输连接，统一产出 (read_stream, write_stream)。

    - stdio：拉起本地子进程，经 stdin/stdout 通信
    - sse：HTTP 长连接（远程 server）
    - http：Streamable HTTP（自建远程 server 的推荐方式）
    """
    match config.transport:
        case "stdio":
            if not config.command:
                raise ValueError(f"server {config.name!r}: stdio 传输需要 command")
            params = StdioServerParameters(command=config.command, args=config.args)
            async with stdio_client(params) as (read, write):
                yield read, write
        case "sse":
            if not config.url:
                raise ValueError(f"server {config.name!r}: sse 传输需要 url")
            async with sse_client(config.url) as (read, write):
                yield read, write
        case "http":
            if not config.url:
                raise ValueError(f"server {config.name!r}: http 传输需要 url")
            async with streamable_http_client(config.url) as (read, write):
                yield read, write
