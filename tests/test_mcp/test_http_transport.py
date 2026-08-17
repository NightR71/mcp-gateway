"""Streamable HTTP 传输集成测试：demo server 以 http 模式独立进程运行。

覆盖 docker compose 部署形态（gateway ←HTTP→ demo-sql 容器）所用的传输路径，
防止 transports.py 的 http 分支因日常只测 stdio 而回归。
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
    raise RuntimeError("demo server http 模式启动超时")


@pytest.fixture
async def http_registry(tmp_path: Path) -> AsyncIterator[ToolRegistry]:
    port = _free_port()
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
        config = MCPServerConfig(
            name="demo_sql_http",
            transport="http",
            url=f"http://127.0.0.1:{port}/mcp",
        )
        registry = ToolRegistry((config,), tool_call_timeout=10)
        await registry.connect_all()
        yield registry
        await registry.close()
    finally:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=5)


async def test_http_transport_list_and_call(http_registry: ToolRegistry) -> None:
    """经 HTTP 传输聚合工具列表并完成一次 NL2SQL 调用。"""
    tools = http_registry.list_tools()
    assert "demo_sql_http__ask" in {t.name for t in tools}

    result = await http_registry.call_tool("demo_sql_http__ask", {"question": "有多少客户？"})
    assert result.is_error is False
    assert "5" in result.content[0]["text"]

    status = http_registry.server_status()
    assert status[0].connected is True
    assert status[0].transport == "http"
