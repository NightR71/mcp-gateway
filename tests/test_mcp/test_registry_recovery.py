"""阶段 7：Registry 失败状态、自动重连与错误分类测试（任务 1/2/4 + 任务 6 停服自愈集成）。

fake client 工厂注入可控的失败/恢复脚本（不拉真实子进程，沙箱/CI 稳定），验证：
- 首连失败 → FailureState 记录（error / attempts / next_retry_at）
- 指数退避间隔计算（base * 2^(n-1)，封顶 retry_max）
- retry_now() 钩子重连成功 → 工具恢复、failure 清除
- 后台重试循环按退避自动恢复（短退避实测）
- mark_failed 摘除 client 与工具并进入重试状态
- close() 取消重试任务
- ServerStatus 新字段默认值
- classify_error 三分（含 MCP SDK 的 MCPError 断连码 -32000 / 读超时码 -32001）
- 任务 6 集成：真实 http demo server kill → 调用触发自愈 → 重启 → 后台循环自动恢复
"""

import asyncio
import os
import socket
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import mcp.types as mcp_types
import pytest
from mcp.shared.exceptions import MCPError
from prometheus_client import REGISTRY

from app.config import MCPServerConfig
from app.mcp.registry import ToolRegistry, classify_error
from app.mcp.schemas import ServerStatus, ToolCallTimeoutError

DEMO_SERVER = Path(__file__).resolve().parents[2] / "servers" / "demo_sql_server" / "server.py"

TOOL = mcp_types.Tool(
    name="echo",
    description="echo a message",
    inputSchema={"type": "object", "properties": {"message": {"type": "string"}}},
)


class FakeClient:
    """可控 fake client：向工厂查询「本次连接是否应失败」（按尝试次数全局消费）。

    调用行为可注入：`call_error`（call 时抛出的异常）、`call_delay`（call 前 sleep 秒数）。
    """

    def __init__(self, name: str, factory: "FakeClientFactory") -> None:
        self.name = name
        self._factory = factory
        self.connect_calls = 0
        self.connected = False
        self.closed = False
        self.call_error: Exception | None = None
        self.call_delay: float = 0.0

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._factory.should_fail(self.name):
            raise ConnectionError(f"connect refused (call {self.connect_calls})")
        self.connected = True

    async def list_tools(self) -> list[mcp_types.Tool]:
        return [TOOL]

    async def call_tool(
        self, tool_name: str, arguments: dict, *, read_timeout: float | None = None
    ):
        if self.call_delay:
            await asyncio.sleep(self.call_delay)
        if self.call_error is not None:
            raise self.call_error
        return mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text="ok")])

    async def close(self) -> None:
        self.closed = True
        self.connected = False


class FakeClientFactory:
    """按 server 名注入「前 N 次连接尝试失败」脚本的工厂（跨实例累计消费）。

    fail_counts={"demo": 1} = demo 的前 1 次 connect 尝试失败，之后全部成功；
    {"demo": 999} = 持续失败（测试失败状态/退避用）。
    """

    def __init__(self, fail_counts: dict[str, int] | None = None) -> None:
        self._remaining = dict(fail_counts or {})
        self.clients: list[FakeClient] = []

    def should_fail(self, name: str) -> bool:
        if self._remaining.get(name, 0) > 0:
            self._remaining[name] -= 1
            return True
        return False

    def __call__(self, config: MCPServerConfig) -> FakeClient:
        client = FakeClient(config.name, self)
        self.clients.append(client)
        return client


def _config(name: str = "demo") -> MCPServerConfig:
    return MCPServerConfig(name=name, transport="inprocess", module="unused")


def _make_registry(
    factory: FakeClientFactory,
    *,
    retry_base: float = 60.0,  # 默认放大的退避，避免后台循环在测试内干扰
    retry_max: float = 120.0,
    tool_call_timeout: float = 30.0,
) -> ToolRegistry:
    return ToolRegistry(
        (_config(),),
        client_factory=factory,
        retry_base=retry_base,
        retry_max=retry_max,
        tool_call_timeout=tool_call_timeout,
    )


@pytest.fixture
async def registry() -> AsyncIterator[ToolRegistry]:
    """正常连接（零失败）的 registry，验证重试字段保持默认值。"""
    r = ToolRegistry((_config(),), client_factory=FakeClientFactory())
    await r.connect_all()
    yield r
    await r.close()


async def test_initial_failure_recorded() -> None:
    """首连失败 → failure 记录：status 可见 error/attempts/next_retry_at，工具为空。"""
    factory = FakeClientFactory({"demo": 999})  # 永远失败
    r = _make_registry(factory)
    await r.connect_all()  # 不应抛异常

    assert r.list_tools() == []
    status = r.server_status()[0]
    assert status.connected is False
    assert "connect refused" in (status.error or "")
    assert status.retry_attempts == 1
    assert status.next_retry_at is not None

    failure = r._failures["demo"]
    assert failure.attempts == 1
    assert failure.error
    assert failure.next_retry_at > time.monotonic()
    await r.close()


async def test_exponential_backoff_delays() -> None:
    """指数退避间隔：2/4/8/16/32/60...，封顶 retry_max。"""
    r = ToolRegistry((), retry_base=2.0, retry_max=60.0)
    assert r._retry_delay(1) == 2.0
    assert r._retry_delay(2) == 4.0
    assert r._retry_delay(3) == 8.0
    assert r._retry_delay(4) == 16.0
    assert r._retry_delay(5) == 32.0
    assert r._retry_delay(6) == 60.0  # 封顶
    assert r._retry_delay(10) == 60.0
    await r.close()


async def test_retry_now_reconnects_and_restores_tools() -> None:
    """retry_now() 测试钩子：重连成功 → 工具恢复、failure 清除、attempts 归零。"""
    factory = FakeClientFactory({"demo": 1})  # 首次失败，重试成功
    r = _make_registry(factory)
    await r.connect_all()
    assert r.server_status()[0].connected is False

    await r.retry_now()

    assert r.server_status()[0].connected is True
    assert r.server_status()[0].error is None
    assert r.server_status()[0].retry_attempts == 0
    assert r.server_status()[0].next_retry_at is None
    assert [t.name for t in r.list_tools()] == ["demo__echo"]
    assert "demo" not in r._failures
    await r.close()


async def test_retry_loop_auto_recovers() -> None:
    """后台重试循环：失败后按退避自动重连并恢复工具（短退避实测）。"""
    factory = FakeClientFactory({"demo": 1})
    r = _make_registry(factory, retry_base=0.05, retry_max=0.2)
    await r.connect_all()
    assert r.server_status()[0].connected is False

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if r.server_status()[0].connected:
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("后台重试循环未在超时内恢复连接")

    status = r.server_status()[0]
    assert status.connected is True
    assert status.retry_attempts == 0
    assert [t.name for t in r.list_tools()] == ["demo__echo"]
    await r.close()


async def test_failures_increment_attempts_with_backoff() -> None:
    """连续失败：attempts 递增、next_retry_at 单调后移（退避生效）。"""
    factory = FakeClientFactory({"demo": 999})
    r = _make_registry(factory)  # retry_base=60，后台循环不会在测试内触发
    await r.connect_all()
    assert r._failures["demo"].attempts == 1
    first_retry_at = r._failures["demo"].next_retry_at

    await r.retry_now()  # 再次失败
    assert r._failures["demo"].attempts == 2
    assert r._failures["demo"].next_retry_at > first_retry_at

    await r.retry_now()  # 第三次失败
    assert r._failures["demo"].attempts == 3
    assert r._failures["demo"].next_retry_at > first_retry_at
    await r.close()


async def test_mark_failed_removes_client_and_tools() -> None:
    """mark_failed：摘除 client 与工具、进入重试状态、异步关闭旧连接。"""
    factory = FakeClientFactory()  # 全部成功
    r = _make_registry(factory)
    await r.connect_all()
    assert r.server_status()[0].connected is True

    r.mark_failed("demo", "boom")

    assert "demo" not in r._clients
    assert r.list_tools() == []
    status = r.server_status()[0]
    assert status.connected is False
    assert status.error == "boom"
    assert status.retry_attempts == 1
    assert status.next_retry_at is not None
    await asyncio.sleep(0)  # 让 fire-and-forget 的 close 任务执行
    assert factory.clients[-1].closed is True
    await r.close()


async def test_close_cancels_retry_task() -> None:
    """close()：取消后台重试任务并清空失败状态。"""
    factory = FakeClientFactory({"demo": 999})
    r = _make_registry(factory)
    await r.connect_all()
    assert r._retry_task is not None
    assert not r._retry_task.done()

    await r.close()

    assert r._retry_task is None
    assert r._failures == {}


async def test_registry_with_no_failures_keeps_defaults(registry: ToolRegistry) -> None:
    """正常连接的 server：重试字段保持默认值（0 / None）。"""
    status = registry.server_status()[0]
    assert status.connected is True
    assert status.retry_attempts == 0
    assert status.next_retry_at is None


def test_server_status_new_field_defaults() -> None:
    """ServerStatus 新字段默认值：旧客户端/旧构造不受影响。"""
    status = ServerStatus(name="s", transport="stdio", connected=False)
    assert status.retry_attempts == 0
    assert status.next_retry_at is None


# ---------- 阶段 7 任务 2：总超时与错误分类 ----------


async def test_classify_error_mapping() -> None:
    """错误分类三分：timeout / connection / tool_error（含 MCP SDK 的 MCPError 码）。"""
    assert classify_error(TimeoutError("t")) == "timeout"  # 3.11+ 与 asyncio.TimeoutError 同义
    # MCP SDK 2.x：传输中断抛 MCPError(CONNECTION_CLOSED=-32000)，读超时抛 REQUEST_TIMEOUT=-32001
    assert classify_error(MCPError(-32000, "Connection closed")) == "connection"
    assert classify_error(MCPError(-32001, "Request 'tools/call' timed out")) == "timeout"
    # 其他 JSON-RPC 错误码（如 Method not found）是协议错误，不触发自愈
    assert classify_error(MCPError(-32601, "Method not found")) == "tool_error"
    assert classify_error(ConnectionError("c")) == "connection"
    assert classify_error(RuntimeError("r")) == "connection"
    assert classify_error(OSError("o")) == "connection"
    assert classify_error(ValueError("v")) == "tool_error"
    assert classify_error(KeyError("k")) == "tool_error"


async def test_call_connection_error_marks_failed_and_removes_tools() -> None:
    """运行期连接类异常 → mark_failed：摘除工具、进入失败状态、异步关闭旧连接。"""
    factory = FakeClientFactory()
    r = _make_registry(factory)
    await r.connect_all()
    factory.clients[-1].call_error = ConnectionError("connection reset")

    with pytest.raises(ConnectionError):
        await r.call_tool("demo__echo", {})

    assert "demo" not in r._clients
    assert r.list_tools() == []
    status = r.server_status()[0]
    assert status.connected is False
    assert status.retry_attempts == 1
    assert "connection reset" in (status.error or "")
    await asyncio.sleep(0)  # fire-and-forget 的 close 任务执行
    assert factory.clients[-1].closed is True
    await r.close()


async def test_call_timeout_raises_tool_call_timeout_error() -> None:
    """总超时 → ToolCallTimeoutError（明确提示）；连接/工具保留；记 exception 指标。"""
    factory = FakeClientFactory()
    r = _make_registry(factory, tool_call_timeout=0.1)
    await r.connect_all()
    factory.clients[-1].call_delay = 0.3  # 超过 0.1s 总时长上限

    before = (
        REGISTRY.get_sample_value(
            "mcp_gateway_tool_calls_total",
            {"tool": "demo__echo", "server": "demo", "status": "exception"},
        )
        or 0.0
    )

    with pytest.raises(ToolCallTimeoutError) as exc_info:
        await r.call_tool("demo__echo", {})

    assert exc_info.value.timeout == 0.1
    assert "工具调用超时（>0.1s）" in str(exc_info.value)
    # 超时不触发自愈：连接与工具保留
    assert r.server_status()[0].connected is True
    assert [t.name for t in r.list_tools()] == ["demo__echo"]
    # 指标：exception 计数 +1（ToolCallTimer 在异常时自动记录）
    after = (
        REGISTRY.get_sample_value(
            "mcp_gateway_tool_calls_total",
            {"tool": "demo__echo", "server": "demo", "status": "exception"},
        )
        or 0.0
    )
    assert after == before + 1
    await r.close()


# ---------- 阶段 7 任务 6：真实停服自愈集成（kill → 自愈 → 重启 → 自动恢复） ----------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("demo server http 模式启动超时")


async def _start_http_server(tmp_path: Path, port: int) -> asyncio.subprocess.Process:
    """拉起 http 模式 demo server（指定端口，供 kill/重启复用同一端口）。"""
    env = {
        **os.environ,
        "DEMO_SQL_TRANSPORT": "http",
        "DEMO_SQL_HOST": "127.0.0.1",
        "DEMO_SQL_PORT": str(port),
        "DEMO_SQL_DB_PATH": str(tmp_path / "demo.db"),
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(DEMO_SERVER),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        await asyncio.to_thread(_wait_port, port)
    except Exception:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=5)
        raise
    return proc


async def test_real_server_kill_and_auto_recovery(tmp_path: Path) -> None:
    """任务 6 验收：真实停服自愈闭环（http 形态，同 Docker compose 部署）。

    kill demo server → 调用工具（SDK 抛 MCPError -32000）→ 分类 connection → mark_failed
    摘工具 → 重启 server（同端口）→ 后台重试循环自动恢复 → 工具可再次调用。
    """
    port = _free_port()
    proc = await _start_http_server(tmp_path, port)
    try:
        config = MCPServerConfig(name="demo", transport="http", url=f"http://127.0.0.1:{port}/mcp")
        r = ToolRegistry((config,), tool_call_timeout=5, retry_base=0.2, retry_max=1.0)
        await r.connect_all()
        assert r.server_status()[0].connected is True
        assert len(r.list_tools()) == 4

        # 1) 停服：kill demo server
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=5)

        # 2) 调用触发 connection 分类 → mark_failed（SDK 抛 MCPError(CONNECTION_CLOSED)）
        with pytest.raises(MCPError):
            await r.call_tool("demo__echo", {"message": "hi"})
        status = r.server_status()[0]
        assert status.connected is False
        assert status.retry_attempts >= 1
        assert r.list_tools() == []

        # 3) 重启 server（同端口）
        await asyncio.sleep(0.3)  # 等端口释放
        proc = await _start_http_server(tmp_path, port)

        # 4) 后台重试循环自动恢复（retry_base=0.2 短退避）
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if r.server_status()[0].connected:
                break
            await asyncio.sleep(0.1)
        status = r.server_status()[0]
        assert status.connected is True, "后台重试循环未在超时内恢复连接"
        assert status.retry_attempts == 0
        assert len(r.list_tools()) == 4

        # 5) 恢复后工具可正常调用
        result = await r.call_tool("demo__echo", {"message": "back"})
        assert result.is_error is False
        assert result.content[0]["text"] == "echo: back"
        await r.close()
    finally:
        if proc.returncode is None:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
