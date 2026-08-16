"""MCP 客户端封装：对一个 MCP Server 的连接、list_tools、call_tool。

连接生命周期由 AsyncExitStack 管理：connect() 建立传输与会话，
close() 逆序清理（会话 → 传输 → 子进程/HTTP 连接）。
"""

from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession

from app.config import MCPServerConfig
from app.core.logging import get_logger
from app.mcp.transports import create_transport

logger = get_logger(__name__)


class MCPClient:
    """单个 MCP Server 的客户端（一个实例对应一条长连接会话）。"""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._exit_stack = AsyncExitStack()
        self._session: ClientSession | None = None

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def connect(self) -> None:
        """建立传输连接并完成 MCP 初始化握手。"""
        read, write = await self._exit_stack.enter_async_context(create_transport(self._config))
        session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        init_result = await session.initialize()
        self._session = session
        logger.info(
            "mcp_server_connected",
            server=self.name,
            transport=self._config.transport,
            server_version=init_result.server_info.name,
        )

    async def list_tools(self) -> list[Any]:
        """列出该 server 的全部工具（mcp.types.Tool 列表）。"""
        session = self._require_session()
        result = await session.list_tools()
        return list(result.tools)

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any], *, read_timeout: float | None = None
    ) -> Any:
        """调用工具，返回 mcp.types.CallToolResult。"""
        session = self._require_session()
        return await session.call_tool(tool_name, arguments, read_timeout_seconds=read_timeout)

    async def close(self) -> None:
        """关闭会话与传输（子进程随之退出）。"""
        await self._exit_stack.aclose()
        self._session = None
        logger.info("mcp_server_disconnected", server=self.name)

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError(f"server {self.name!r} 尚未连接，请先调用 connect()")
        return self._session
