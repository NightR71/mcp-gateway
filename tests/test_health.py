"""健康检查与指标接口测试。"""

from httpx import AsyncClient


async def test_health_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app_name"] == "mcp-gateway"
    assert body["version"]


async def test_metrics_exposed(client: AsyncClient) -> None:
    await client.get("/health")  # 先产生一次请求，确保计数器已注册
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "http_requests_total" in resp.text
