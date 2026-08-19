"""进程内（inprocess）传输测试：不拉子进程，直接在网关进程加载 demo_sql server。

对应 Vercel Serverless 场景：子进程解释器看不到构建期依赖（ModuleNotFoundError: mcp），
进程内加载与网关共用同一份 site-packages，从机制上消除该问题。
"""

from collections.abc import AsyncIterator

import pytest

from app.config import MCPServerConfig, get_server_configs
from app.mcp.registry import ToolRegistry

VERCEL_CONFIG = "config/gateway.vercel.yaml"


def _demo_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="demo_sql",
        transport="inprocess",
        module="servers.demo_sql_server.server:server",
    )


@pytest.fixture
async def registry() -> AsyncIterator[ToolRegistry]:
    r = ToolRegistry((_demo_config(),))
    await r.connect_all()
    yield r
    await r.close()


async def test_aggregate_tools_and_call(registry: ToolRegistry) -> None:
    """工具聚合：4 个 demo_sql 工具全部注册，echo 直接调用成功。"""
    tools = registry.list_tools()
    assert {t.name for t in tools} == {
        "demo_sql__echo",
        "demo_sql__ask",
        "demo_sql__run_sql",
        "demo_sql__list_tables",
    }
    echo = next(t for t in tools if t.name == "demo_sql__echo")
    assert "message" in echo.input_schema["properties"]

    result = await registry.call_tool("demo_sql__echo", {"message": "hello"})
    assert result.is_error is False
    assert result.content[0]["text"] == "echo: hello"

    status = registry.server_status()
    assert status[0].transport == "inprocess"
    assert status[0].connected is True
    assert status[0].tool_count == 4
    assert status[0].error is None


async def test_ask_nl2sql(registry: ToolRegistry) -> None:
    """NL2SQL 闭环：自然语言 → SQL → SQLite 执行（进程内惰性建库 + 种子）。"""
    result = await registry.call_tool("demo_sql__ask", {"question": "有多少客户？"})
    assert result.is_error is False
    text = result.content[0]["text"]
    assert "SELECT COUNT(*)" in text
    assert "| 5 |" in text


async def test_bad_module_does_not_block() -> None:
    """module 指向不存在时连接失败被记录，不阻塞整体启动。"""
    bad = MCPServerConfig(name="bad", transport="inprocess", module="no.such.module:server")
    r = ToolRegistry((bad,))
    await r.connect_all()  # 不应抛异常

    assert r.list_tools() == []
    status = r.server_status()
    assert status[0].connected is False
    assert status[0].error
    await r.close()


async def test_vercel_config_loads_inprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """直接加载生产 Vercel 配置：demo_sql 必须以 inprocess 连接并注册 4 个工具。"""
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", VERCEL_CONFIG)
    get_server_configs.cache_clear()
    configs = get_server_configs()

    assert len(configs) == 1
    assert configs[0].transport == "inprocess"
    assert configs[0].module == "servers.demo_sql_server.server:server"

    r = ToolRegistry(configs)
    await r.connect_all()
    assert len(r.list_tools()) == 4
    status = r.server_status()
    assert status[0].connected is True
    assert status[0].error is None
    await r.close()
