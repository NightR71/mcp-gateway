"""MCP 客户端封装：对一个 MCP Server 的连接、list_tools、call_tool。

连接生命周期由 AsyncExitStack 管理：connect() 建立传输与会话，
close() 逆序清理（会话 → 传输 → 子进程/HTTP 连接）。

另有进程内客户端 InProcessClient：不拉子进程/网络，直接持有本进程内的
MCPServer 实例并调其公开 API，用于 Serverless（如 Vercel）等子进程解释器
依赖环境不可控的场景。create_client() 按配置选择实现。
"""

import importlib
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.server.mcpserver import MCPServer

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


class InProcessClient:
    """进程内 MCP Server 客户端：复用本进程的 MCPServer 实例，无任何子进程/网络。

    接口与 MCPClient 一致（name / connected / connect / list_tools / call_tool / close），
    registry 无需区分。连接即「按 config.module 导入模块并取出 MCPServer 实例」，
    依赖与网关同一份 site-packages，彻底消除子进程解释器环境差异。
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._server: MCPServer | None = None

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def connected(self) -> bool:
        return self._server is not None

    async def connect(self) -> None:
        """导入 config.module（"package.module:attr"，attr 缺省 server）并取出 MCPServer 实例。"""
        if not self._config.module:
            raise ValueError(f"server {self.name!r}: inprocess 传输需要 module")
        module_name, _, attr = self._config.module.partition(":")
        module = importlib.import_module(module_name)
        server = getattr(module, attr or "server", None)
        if not isinstance(server, MCPServer):
            raise TypeError(f"server {self.name!r}: {self._config.module} 不是 MCPServer 实例")
        self._server = server
        logger.info(
            "mcp_server_connected",
            server=self.name,
            transport="inprocess",
            server_version=server.name,
        )

    async def list_tools(self) -> list[Any]:
        """列出该 server 的全部工具（MCPServer.list_tools 的公开 API）。"""
        return list(await self._require_server().list_tools())

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any], *, read_timeout: float | None = None
    ) -> Any:
        """直接调用工具（进程内无读写超时概念，read_timeout 仅为接口对齐而忽略）。"""
        return await self._require_server().call_tool(tool_name, arguments)

    async def close(self) -> None:
        """释放实例引用（无连接需要清理，仅为接口对称与日志闭环）。"""
        self._server = None
        logger.info("mcp_server_disconnected", server=self.name)

    def _require_server(self) -> MCPServer:
        if self._server is None:
            raise RuntimeError(f"server {self.name!r} 尚未连接，请先调用 connect()")
        return self._server


def create_client(config: MCPServerConfig) -> MCPClient | InProcessClient:
    """按 transport 选择客户端实现：inprocess 走进程内，其余走真实传输连接。"""
    if config.transport == "inprocess":
        return InProcessClient(config)
    return MCPClient(config)
