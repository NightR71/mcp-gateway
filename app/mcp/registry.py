"""工具注册中心（网关核心）：聚合所有 MCP Server 的工具。

职责：
- 启动时按 YAML 声明并发连接所有 enabled server（单个失败不阻塞整体）
- 维护「命名空间工具名 → (server, 原始工具名)」映射，保证工具名全局唯一
- 对上层提供统一的 list_tools / call_tool / server_status
"""

import asyncio
from typing import Any

import mcp.types as mcp_types

from app.config import MCPServerConfig
from app.core.logging import get_logger
from app.core.metrics import ToolCallTimer
from app.mcp.client import MCPClient
from app.mcp.schemas import (
    NAMESPACE_SEPARATOR,
    ServerStatus,
    ToolCallResult,
    ToolInfo,
    UnknownToolError,
)

logger = get_logger(__name__)


class ToolRegistry:
    """多 MCP Server 的工具聚合与路由。"""

    def __init__(
        self, server_configs: tuple[MCPServerConfig, ...], *, tool_call_timeout: float = 30.0
    ) -> None:
        self._configs = {c.name: c for c in server_configs if c.enabled}
        self._tool_call_timeout = tool_call_timeout
        self._clients: dict[str, MCPClient] = {}
        self._tools: dict[str, ToolInfo] = {}
        self._tool_server: dict[str, str] = {}  # 命名空间工具名 -> server 名
        self._errors: dict[str, str] = {}  # 连接失败的 server -> 错误信息

    async def connect_all(self) -> None:
        """并发连接所有 enabled server；失败的记为不可用，不影响其他 server。"""
        if not self._configs:
            logger.info("registry_no_servers")
            return
        # _connect_one 内部捕获全部异常并记入 self._errors，此处 gather 不会抛出
        await asyncio.gather(*(self._connect_one(name, cfg) for name, cfg in self._configs.items()))
        logger.info(
            "registry_ready",
            total=len(self._configs),
            connected=len(self._clients),
            failed=len(self._errors),
            tools=len(self._tools),
        )

    async def _connect_one(self, name: str, config: MCPServerConfig) -> None:
        client = MCPClient(config)
        try:
            await client.connect()
            tools = await client.list_tools()
        except Exception as exc:
            await client.close()
            self._errors[name] = str(exc)
            logger.error("mcp_server_connect_failed", server=name, error=str(exc))
            return
        self._clients[name] = client
        for tool in tools:
            self._register_tool(name, tool)

    def _register_tool(self, server_name: str, tool: mcp_types.Tool) -> None:
        namespaced = f"{server_name}{NAMESPACE_SEPARATOR}{tool.name}"
        if namespaced in self._tools:
            logger.warning("tool_name_conflict", tool=namespaced, server=server_name)
            return
        self._tools[namespaced] = ToolInfo(
            name=namespaced,
            original_name=tool.name,
            server=server_name,
            description=tool.description or "",
            input_schema=dict(tool.input_schema),
        )
        self._tool_server[namespaced] = server_name

    def list_tools(self) -> list[ToolInfo]:
        """聚合后的全部工具（含命名空间前缀）。"""
        return list(self._tools.values())

    def get_tool(self, namespaced_name: str) -> ToolInfo:
        try:
            return self._tools[namespaced_name]
        except KeyError:
            raise UnknownToolError(namespaced_name) from None

    async def call_tool(self, namespaced_name: str, arguments: dict[str, Any]) -> ToolCallResult:
        """按命名空间工具名路由到对应 server 调用（全程记录 Prometheus 指标）。"""
        tool = self.get_tool(namespaced_name)
        client = self._clients[tool.server]
        with ToolCallTimer(namespaced_name, tool.server) as timer:
            result: mcp_types.CallToolResult = await client.call_tool(
                tool.original_name, arguments, read_timeout=self._tool_call_timeout
            )
            if result.is_error:
                timer.status = "error"
        return ToolCallResult(
            content=[c.model_dump(mode="json") for c in result.content],
            is_error=result.is_error,
        )

    def server_status(self) -> list[ServerStatus]:
        """所有声明 server 的连接状态（含失败的）。"""
        statuses = []
        for name, config in self._configs.items():
            connected = name in self._clients
            statuses.append(
                ServerStatus(
                    name=name,
                    transport=config.transport,
                    connected=connected,
                    tool_count=sum(1 for s in self._tool_server.values() if s == name),
                    error=None if connected else self._errors.get(name, "未连接"),
                )
            )
        return statuses

    async def close(self) -> None:
        """关闭所有连接（网关 shutdown 时调用）。"""
        await asyncio.gather(
            *(client.close() for client in self._clients.values()), return_exceptions=True
        )
        self._clients.clear()
        self._tools.clear()
        self._tool_server.clear()
