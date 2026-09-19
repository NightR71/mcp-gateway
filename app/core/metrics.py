"""Prometheus 指标：HTTP 层由 instrumentator 自动采集，工具调用层由 registry 手动记录。

自定义指标（随 /metrics 暴露，暴露策略见 routes/metrics.py）：
- mcp_gateway_tool_calls_total{tool,server,status}      Counter   工具调用次数
- mcp_gateway_tool_call_duration_seconds{tool,server}   Histogram 工具调用耗时

status 取值：ok（成功）/ error（下游返回 is_error）/ exception（调用抛异常）。
prometheus_client 指标为进程级全局，多次调用 record_tool_call 会累积。

M1 §5.3：/metrics 不再由 instrumentator 直接暴露——本模块只负责采集中间件
与指标文本生成，端点按 MetricsConfig（默认关闭 + 可选鉴权）在 routes/metrics.py 挂载。
"""

import time

from fastapi import FastAPI, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_fastapi_instrumentator import Instrumentator

TOOL_CALLS_TOTAL = Counter(
    "mcp_gateway_tool_calls_total",
    "MCP 工具调用总次数（按工具/所属 server/结果状态细分）",
    ["tool", "server", "status"],
)
TOOL_CALL_DURATION = Histogram(
    "mcp_gateway_tool_call_duration_seconds",
    "MCP 工具调用耗时（秒）",
    ["tool", "server"],
)


def setup_metrics(app: FastAPI) -> None:
    """采集 HTTP 请求数/延迟等默认指标（不暴露端点；HTTP 指标进全局 REGISTRY）。"""
    Instrumentator().instrument(app)


def metrics_exposition() -> Response:
    """当前进程的 Prometheus 文本指标（HTTP 中间件指标 + 自定义工具指标）。"""
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


def record_tool_call(tool: str, server: str, status: str, duration_seconds: float) -> None:
    """记录一次工具调用的次数与耗时（由 ToolRegistry.call_tool 调用）。"""
    TOOL_CALLS_TOTAL.labels(tool=tool, server=server, status=status).inc()
    TOOL_CALL_DURATION.labels(tool=tool, server=server).observe(duration_seconds)


class ToolCallTimer:
    """工具调用计时器：with 语句退出时自动记录指标。

    用法（registry.call_tool）：
        with ToolCallTimer(tool, server) as timer:
            result = await client.call_tool(...)
        timer.status 默认 "ok"，下游 is_error 时置 "error"；异常抛出时自动记 "exception"。
    """

    def __init__(self, tool: str, server: str) -> None:
        self.tool = tool
        self.server = server
        self.status = "ok"
        self._start = 0.0

    def __enter__(self) -> "ToolCallTimer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type: object, *_: object) -> bool:
        if exc_type is not None:
            self.status = "exception"
        record_tool_call(self.tool, self.server, self.status, time.perf_counter() - self._start)
        return False  # 不吞异常
