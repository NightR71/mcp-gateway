"""SSE 传输集成测试：demo server 以 sse 模式独立进程运行（阶段 7 任务 5）。

补齐 §2 能力表中「SSE 无测试」的缺口：与 test_http_transport.py 同构——
真实子进程 + 真实传输连接，验证 transports.py 的 sse 分支（sse_client）与
registry 全链路（list_tools / call_tool / server_status）。
"""

import asyncio
import os
import socket
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.config import MCPServerConfig
from app.mcp.registry import ToolRegistry

DEMO_SERVER = Path(__file__).resolve().parents[2] / "servers" / "demo_sql_server" / "server.py"


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
    raise RuntimeError("demo server sse 模式启动超时")


async def _start_sse_server(tmp_path: Path) -> tuple[asyncio.subprocess.Process, int]:
    """拉起 demo server（sse 模式），返回 (进程, 端口)。"""
    port = _free_port()
    env = {
        **os.environ,
        "DEMO_SQL_TRANSPORT": "sse",
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
    return proc, port


async def _stop_server(proc: asyncio.subprocess.Process) -> None:
    proc.terminate()
    await asyncio.wait_for(proc.wait(), timeout=5)


@pytest.fixture
async def sse_registry(tmp_path: Path) -> AsyncIterator[ToolRegistry]:
    """经 registry 连接本地 SSE server 的集成夹具。"""
    proc, port = await _start_sse_server(tmp_path)
    try:
        config = MCPServerConfig(
            name="demo_sql_sse",
            transport="sse",
            url=f"http://127.0.0.1:{port}/sse",
        )
        registry = ToolRegistry((config,), tool_call_timeout=10)
        await registry.connect_all()
        yield registry
        await registry.close()
    finally:
        await _stop_server(proc)


async def test_sse_transport_list_and_call(sse_registry: ToolRegistry) -> None:
    """经 SSE 传输聚合工具列表并完成一次工具调用（registry 全链路）。"""
    tools = sse_registry.list_tools()
    assert {t.name for t in tools} == {
        "demo_sql_sse__echo",
        "demo_sql_sse__ask",
        "demo_sql_sse__run_sql",
        "demo_sql_sse__list_tables",
    }

    result = await sse_registry.call_tool("demo_sql_sse__echo", {"message": "hello"})
    assert result.is_error is False
    assert result.content[0]["text"] == "echo: hello"

    status = sse_registry.server_status()
    assert status[0].connected is True
    assert status[0].transport == "sse"
    assert status[0].error is None
    assert status[0].tool_count == 4


async def test_sse_client_minimal_smoke(tmp_path: Path) -> None:
    """最小冒烟（SDK 2.0 直接路径）：sse_client 建立会话 → initialize → list_tools。"""
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    proc, port = await _start_sse_server(tmp_path)
    try:
        async with sse_client(f"http://127.0.0.1:{port}/sse") as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.server_info.name == "demo_sql_server"
                tools = await session.list_tools()
                assert len(tools.tools) == 4
    finally:
        await _stop_server(proc)
