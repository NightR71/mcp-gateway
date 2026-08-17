"""工具调用指标测试：Counter/Histogram 记录与 /metrics 暴露。

prometheus_client 的指标为进程级全局且测试间累积，断言一律用差值。
"""

from httpx import AsyncClient
from prometheus_client import REGISTRY

from app.core.metrics import record_tool_call


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


async def test_metrics_endpoint_exposes_tool_metrics(client: AsyncClient) -> None:
    record_tool_call("t_http", "s_http", "ok", 0.01)

    resp = await client.get("/metrics")

    assert resp.status_code == 200
    # Prometheus  exposition 中 label 按字典序排列：server, status, tool
    assert 'mcp_gateway_tool_calls_total{server="s_http",status="ok",tool="t_http"}' in resp.text
    assert "mcp_gateway_tool_call_duration_seconds_count" in resp.text
