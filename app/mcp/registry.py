"""工具注册中心（网关核心）：聚合所有 MCP Server 的工具。

职责：
- 启动时按 YAML 声明并发连接所有 enabled server（单个失败不阻塞整体）
- 维护「命名空间工具名 → (server, 原始工具名)」映射，保证工具名全局唯一
- 对上层提供统一的 list_tools / call_tool / server_status
- 阶段 7：失败 server 进入 `_failures`（FailureState），后台 `_retry_loop`
  按指数退避自动重连；重连成功自动恢复 client 与工具，无需重启网关
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import mcp.types as mcp_types

from app.config import MCPServerConfig
from app.core.logging import get_logger
from app.core.metrics import ToolCallTimer
from app.mcp.client import InProcessClient, MCPClient, create_client
from app.mcp.schemas import (
    NAMESPACE_SEPARATOR,
    ServerStatus,
    ToolCallResult,
    ToolInfo,
    ToolNotAllowedError,
    UnknownToolError,
)
from app.schemas.auth import APIKeyInfo

logger = get_logger(__name__)


@dataclass
class FailureState:
    """一个 server 的失败状态（供自动重连与 /servers 展示）。"""

    error: str  # 最近一次失败原因
    attempts: int = 1  # 已连续失败的次数（决定下次退避间隔）
    next_retry_at: float = 0.0  # 下次自动重试时间（time.monotonic() 秒）


class ToolRegistry:
    """多 MCP Server 的工具聚合与路由。"""

    def __init__(
        self,
        server_configs: tuple[MCPServerConfig, ...],
        *,
        tool_call_timeout: float = 30.0,
        client_factory: Callable[[MCPServerConfig], MCPClient | InProcessClient] = create_client,
        retry_base: float = 2.0,
        retry_max: float = 60.0,
    ) -> None:
        """构造注册中心。

        - `client_factory`：按配置创建客户端（默认 create_client；测试可注入 fake）。
        - `retry_base` / `retry_max`：指数退避参数（间隔 = min(retry_max, retry_base * 2^(n-1))）。
        - 默认值与旧签名完全一致，既有调用方零改动。
        """
        self._configs = {c.name: c for c in server_configs if c.enabled}
        self._tool_call_timeout = tool_call_timeout
        self._client_factory = client_factory
        self._retry_base = retry_base
        self._retry_max = retry_max
        self._clients: dict[str, MCPClient | InProcessClient] = {}
        self._tools: dict[str, ToolInfo] = {}
        self._tool_server: dict[str, str] = {}  # 命名空间工具名 -> server 名
        self._failures: dict[str, FailureState] = {}  # 失败 server -> 失败状态
        self._retry_wake = asyncio.Event()  # 有新失败时唤醒重试循环
        self._retry_task: asyncio.Task[None] | None = None

    async def connect_all(self) -> None:
        """并发连接所有 enabled server；失败的记为不可用并进入自动重试，不影响其他 server。"""
        if not self._configs:
            logger.info("registry_no_servers")
            return
        # _connect_one 内部捕获全部异常并记入 self._failures，此处 gather 不会抛出
        await asyncio.gather(*(self._connect_one(name, cfg) for name, cfg in self._configs.items()))
        self._ensure_retry_task()
        logger.info(
            "registry_ready",
            total=len(self._configs),
            connected=len(self._clients),
            failed=len(self._failures),
            tools=len(self._tools),
        )

    async def _connect_one(self, name: str, config: MCPServerConfig) -> None:
        client = self._client_factory(config)
        try:
            await client.connect()
            tools = await client.list_tools()
        except Exception as exc:
            await client.close()
            self._fail(name, exc)
            return
        self._clients[name] = client
        for tool in tools:
            self._register_tool(name, tool)
        if name in self._failures:
            attempts = self._failures[name].attempts
            del self._failures[name]
            logger.info("mcp_server_reconnected", server=name, attempts=attempts, tools=len(tools))

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

    def _fail(self, name: str, exc: Exception) -> None:
        """记录一次连接失败（连接阶段失败走这里，进入指数退避重试）。"""
        self._record_failure(name, str(exc))
        logger.error("mcp_server_connect_failed", server=name, error=str(exc))

    def mark_failed(self, name: str, error: str) -> None:
        """将 server 标记为失败（阶段 7）：摘除其 client 与工具并进入指数退避重试。

        供运行期调用失败（如任务 2 的连接类异常）时使用；摘除后该 server
        的工具立即从 list_tools 消失，由后台重试循环负责恢复。
        """
        client = self._clients.pop(name, None)
        if client is not None:
            asyncio.get_running_loop().create_task(client.close())
        for tool_name in [t for t, s in self._tool_server.items() if s == name]:
            self._tools.pop(tool_name, None)
            self._tool_server.pop(tool_name, None)
        self._record_failure(name, error)
        logger.error("mcp_server_marked_failed", server=name, error=error)

    def _record_failure(self, name: str, error: str) -> None:
        """记录失败状态：递增 attempts、计算下次退避重试时间并唤醒重试循环。"""
        attempts = self._failures[name].attempts + 1 if name in self._failures else 1
        self._failures[name] = FailureState(
            error=error,
            attempts=attempts,
            next_retry_at=time.monotonic() + self._retry_delay(attempts),
        )
        self._ensure_retry_task()
        self._retry_wake.set()

    def _retry_delay(self, attempts: int) -> float:
        """指数退避间隔：base * 2^(attempts-1)，封顶 retry_max。"""
        return min(self._retry_max, self._retry_base * (2 ** (attempts - 1)))

    def _ensure_retry_task(self) -> None:
        """确保后台重试循环已启动（connect_all 后或运行期首次失败时）。"""
        if self._retry_task is None or self._retry_task.done():
            self._retry_task = asyncio.create_task(self._retry_loop())

    async def _retry_loop(self) -> None:
        """后台重试循环：对失败 server 按指数退避自动重连（close() 时取消）。"""
        logger.info("registry_retry_started", servers=list(self._failures))
        while True:
            next_at = self._next_retry_at()
            if next_at is None:
                # 无失败 server：挂起等待唤醒（mark_failed / _fail 会 set）
                await self._retry_wake.wait()
                self._retry_wake.clear()
                continue
            delay = max(0.0, next_at - time.monotonic())
            try:
                await asyncio.wait_for(self._retry_wake.wait(), timeout=delay)
                self._retry_wake.clear()
            except TimeoutError:
                pass  # 到达最早重试时间
            await self._retry_due()

    def _next_retry_at(self) -> float | None:
        """所有失败 server 中最早的重试时间；无失败时返回 None。"""
        return min((s.next_retry_at for s in self._failures.values()), default=None)

    async def _retry_due(self) -> None:
        """对已到重试时间的失败 server 逐个重连（串行，避免重连风暴）。"""
        now = time.monotonic()
        due = [name for name, state in self._failures.items() if state.next_retry_at <= now]
        for name in due:
            config = self._configs.get(name)
            if config is None:
                continue
            await self._connect_one(name, config)

    async def retry_now(self) -> None:
        """测试钩子：立即对全部失败 server 重连一次（不等退避间隔）。"""
        for name in list(self._failures):
            config = self._configs.get(name)
            if config is not None:
                await self._connect_one(name, config)

    def list_tools(self) -> list[ToolInfo]:
        """聚合后的全部工具（含命名空间前缀）。"""
        return list(self._tools.values())

    def list_tools_for(self, key: APIKeyInfo) -> list[ToolInfo]:
        """按 API Key 的工具白名单过滤（阶段 6）。

        - `allowed_tools is None`（未配置/旧数据）= 不限制，返回全量；
        - 否则只返回白名单内（{server}__{tool} 全名）的工具。
        路由层直接消费本方法，不在路由里重复实现白名单业务逻辑。
        """
        if key.allowed_tools is None:
            return self.list_tools()
        allowed = set(key.allowed_tools)
        return [tool for tool in self._tools.values() if tool.name in allowed]

    def get_tool_for(self, key: APIKeyInfo, namespaced_name: str) -> ToolInfo:
        """按 API Key 取工具（含可见性校验）。

        不存在 → `UnknownToolError`（API 层 404）；真实存在但白名单外 →
        `ToolNotAllowedError`（API 层 403）。
        """
        tool = self.get_tool(namespaced_name)
        if key.allowed_tools is not None and tool.name not in key.allowed_tools:
            raise ToolNotAllowedError(namespaced_name)
        return tool

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

    def server_status(self, key: APIKeyInfo | None = None) -> list[ServerStatus]:
        """所有声明 server 的连接状态（含失败的与重试进度）。

        `key` 为 None 时 tool_count 统计全量工具（兼容旧调用）；传入 Key 时
        按该 Key 可见的工具数统计（阶段 6 白名单）。
        阶段 7：失败 server 输出 `retry_attempts` 与 `next_retry_at`（monotonic 秒）。
        """
        visible = None if key is None else {t.name for t in self.list_tools_for(key)}
        statuses = []
        for name, config in self._configs.items():
            connected = name in self._clients
            failure = self._failures.get(name)
            statuses.append(
                ServerStatus(
                    name=name,
                    transport=config.transport,
                    connected=connected,
                    tool_count=sum(
                        1
                        for tool_name, srv in self._tool_server.items()
                        if srv == name and (visible is None or tool_name in visible)
                    ),
                    error=None if connected else (failure.error if failure else "未连接"),
                    retry_attempts=failure.attempts if failure else 0,
                    next_retry_at=failure.next_retry_at if failure else None,
                )
            )
        return statuses

    async def close(self) -> None:
        """关闭所有连接并停止自动重连（网关 shutdown 时调用）。"""
        if self._retry_task is not None:
            self._retry_task.cancel()
            try:
                await self._retry_task
            except asyncio.CancelledError:
                pass
            self._retry_task = None
        await asyncio.gather(
            *(client.close() for client in self._clients.values()), return_exceptions=True
        )
        self._clients.clear()
        self._tools.clear()
        self._tool_server.clear()
        self._failures.clear()
