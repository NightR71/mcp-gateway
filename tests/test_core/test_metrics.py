"""工具调用指标测试：Counter/Histogram 记录与 /metrics 暴露策略（M1）。

prometheus_client 的指标为进程级全局且测试间累积，断言一律用差值。
/metrics 策略：默认 404（test_health.py）；测试配置（gateway_test.yaml）开启
enabled + require_auth，此处覆盖鉴权语义；require_auth=false 的匿名放行走 monkeypatch。
"""

from httpx import AsyncClient
from prometheus_client import REGISTRY

from app.config import MetricsConfig
from app.core.metrics import record_tool_call

AUTH_HEADERS = {"X-API-Key": "test-key"}
LIMITED_HEADERS = {"X-API-Key": "limited-key"}


def _calls_value(tool: str, server: str, status: str) -> float:
    return (
        REGISTRY.get_sample_value(
            "mcp_gateway_tool_calls_total",
            {"tool": tool, "server": server, "status": status},
        )
        or 0.0
    )


def _duration_count(tool: str, server: str) -> float:
    return (
        REGISTRY.get_sample_value(
            "mcp_gateway_tool_call_duration_seconds_count",
            {"tool": tool, "server": server},
        )
        or 0.0
    )


def test_record_tool_call_increments_counter_and_histogram() -> None:
    tool, server = "t_unit", "s_unit"
    before_ok = _calls_value(tool, server, "ok")
    before_err = _calls_value(tool, server, "exception")
    before_count = _duration_count(tool, server)

    record_tool_call(tool, server, "ok", 0.12)
    record_tool_call(tool, server, "exception", 0.34)
    record_tool_call(tool, server, "ok", 1.5)

    assert _calls_value(tool, server, "ok") == before_ok + 2
    assert _calls_value(tool, server, "exception") == before_err + 1
    assert _duration_count(tool, server) == before_count + 3


async def test_metrics_endpoint_exposes_tool_metrics(gateway_client: AsyncClient) -> None:
    """开启 + 鉴权配置下：携带有效 Key 可读取，HTTP 与自定义工具指标都在。"""
    record_tool_call("t_http", "s_http", "ok", 0.01)

    resp = await gateway_client.get("/metrics", headers=AUTH_HEADERS)

    assert resp.status_code == 200
    assert "http_requests_total" in resp.text
    # Prometheus exposition 中 label 按字典序排列：server, status, tool
    assert 'mcp_gateway_tool_calls_total{server="s_http",status="ok",tool="t_http"}' in resp.text
    assert "mcp_gateway_tool_call_duration_seconds_count" in resp.text


async def test_metrics_unauthorized(gateway_client: AsyncClient) -> None:
    """require_auth=true：无 Key / 错误 Key 一律 401（语义与其他受保护接口一致）。"""
    assert (await gateway_client.get("/metrics")).status_code == 401
    assert (await gateway_client.get("/metrics", headers={"X-API-Key": "wrong"})).status_code == 401


async def test_metrics_rate_limited(gateway_client: AsyncClient) -> None:
    """require_auth=true：metrics 读取同样受限流约束（limited-key 第 3 次 429）。"""
    assert (await gateway_client.get("/metrics", headers=LIMITED_HEADERS)).status_code == 200
    assert (await gateway_client.get("/metrics", headers=LIMITED_HEADERS)).status_code == 200
    resp = await gateway_client.get("/metrics", headers=LIMITED_HEADERS)
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) >= 1


async def test_metrics_public_when_auth_not_required(client: AsyncClient, monkeypatch) -> None:
    """enabled=true + require_auth=false：匿名可读（本机/内网自行选择）。"""
    from app.api.routes import metrics as metrics_route

    monkeypatch.setattr(
        metrics_route, "get_metrics_config", lambda: MetricsConfig(enabled=True, require_auth=False)
    )
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "http_requests_total" in resp.text
