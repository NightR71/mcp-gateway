"""MCP Server 状态查询 API 测试：成功 / 401 未授权。"""

from httpx import AsyncClient

AUTH_HEADERS = {"X-API-Key": "test-key"}


async def test_list_servers_ok(gateway_client: AsyncClient) -> None:
    resp = await gateway_client.get("/servers", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    servers = resp.json()
    assert len(servers) == 1
    assert servers[0]["name"] == "demo_sql"
    # 测试夹具 2026-08-24 起用 inprocess 传输（文档 §14：集成测试优先 inprocess，
    # 沙箱/CI 稳定）；stdio 真实子进程路径由 tests/test_mcp/test_registry.py 覆盖
    assert servers[0]["transport"] == "inprocess"
    assert servers[0]["connected"] is True
    assert servers[0]["tool_count"] == 4  # echo / ask / run_sql / list_tables
    assert servers[0]["error"] is None


async def test_list_servers_unauthorized(gateway_client: AsyncClient) -> None:
    assert (await gateway_client.get("/servers")).status_code == 401
    resp = await gateway_client.get("/servers", headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 401
