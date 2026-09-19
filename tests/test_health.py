"""健康检查与指标接口测试。"""

from httpx import AsyncClient


async def test_health_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app_name"] == "mcp-gateway"
    assert body["version"]


async def test_metrics_disabled_by_default(client: AsyncClient) -> None:
    """M1 §5.3：/metrics 默认关闭公网暴露——默认配置下 404，与不存在路由不可区分。"""
    await client.get("/health")  # 先产生一次请求（指标照常采集）
    resp = await client.get("/metrics")
    assert resp.status_code == 404
