"""阶段 7 任务 1：Registry 失败状态与自动重连机制测试。

用 fake client 工厂注入可控的失败/恢复脚本（不拉真实子进程，沙箱/CI 稳定），验证：
- 首连失败 → FailureState 记录（error / attempts / next_retry_at）
- 指数退避间隔计算（base * 2^(n-1)，封顶 retry_max）
- retry_now() 钩子重连成功 → 工具恢复、failure 清除
- 后台重试循环按退避自动恢复（短退避实测）
- mark_failed 摘除 client 与工具并进入重试状态
- close() 取消重试任务
- ServerStatus 新字段默认值
"""

import asyncio
import time
from collections.abc import AsyncIterator

import mcp.types as mcp_types
import pytest

from app.config import MCPServerConfig
from app.mcp.registry import ToolRegistry
from app.mcp.schemas import ServerStatus

TOOL = mcp_types.Tool(
    name="echo",
    description="echo a message",
    inputSchema={"type": "object", "properties": {"message": {"type": "string"}}},
)


class FakeClient:
    """可控 fake client：向工厂查询「本次连接是否应失败」（按尝试次数全局消费）。"""

    def __init__(self, name: str, factory: "FakeClientFactory") -> None:
        self.name = name
        self._factory = factory
        self.connect_calls = 0
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._factory.should_fail(self.name):
            raise ConnectionError(f"connect refused (call {self.connect_calls})")
        self.connected = True

    async def list_tools(self) -> list[mcp_types.Tool]:
        return [TOOL]

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
) -> ToolRegistry:
    return ToolRegistry(
        (_config(),), client_factory=factory, retry_base=retry_base, retry_max=retry_max
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
