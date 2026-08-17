"""工具注册中心集成测试：用真实 stdio 子进程跑 demo_sql_server。

验证阶段 2 验收标准：网关能连上 demo server，并在 registry 中列出/调用其工具。
"""

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.config import MCPServerConfig
from app.mcp.registry import ToolRegistry
from app.mcp.schemas import UnknownToolError

DEMO_SERVER = Path(__file__).resolve().parents[2] / "servers" / "demo_sql_server" / "server.py"


def _demo_config() -> MCPServerConfig:
    # 用当前解释器拉起 server 子进程：跨平台，且 CI 上无需 uv
    return MCPServerConfig(
        name="demo_sql",
        transport="stdio",
        command=sys.executable,
        args=[str(DEMO_SERVER)],
    )


@pytest.fixture
async def registry() -> AsyncIterator[ToolRegistry]:
    r = ToolRegistry((_demo_config(),))
    await r.connect_all()
    yield r
    await r.close()


async def test_aggregate_tools_and_call(registry: ToolRegistry) -> None:
    """工具聚合：命名空间前缀、schema 透传、调用路由、状态统计。"""
    tools = registry.list_tools()
    assert {t.name for t in tools} == {
        "demo_sql__echo",
        "demo_sql__ask",
        "demo_sql__run_sql",
        "demo_sql__list_tables",
    }
    tool = next(t for t in tools if t.name == "demo_sql__echo")
    assert tool.original_name == "echo"
    assert tool.server == "demo_sql"
    assert "message" in tool.input_schema["properties"]

    result = await registry.call_tool("demo_sql__echo", {"message": "hello"})
    assert result.is_error is False
    assert result.content[0]["text"] == "echo: hello"

    status = registry.server_status()
    assert len(status) == 1
    assert status[0].connected is True
    assert status[0].tool_count == 4
    assert status[0].error is None


async def test_unknown_tool_raises(registry: ToolRegistry) -> None:
    with pytest.raises(UnknownToolError):
        await registry.call_tool("demo_sql__not_exist", {})


async def test_failed_server_does_not_block() -> None:
    """单个 server 连接失败不阻塞整体启动，状态里可见错误。"""
    bad = MCPServerConfig(name="bad", transport="stdio", command="definitely-not-exist-cmd-xxx")
    r = ToolRegistry((bad,))
    await r.connect_all()  # 不应抛异常

    assert r.list_tools() == []
    status = r.server_status()
    assert status[0].connected is False
    assert status[0].error
    await r.close()
