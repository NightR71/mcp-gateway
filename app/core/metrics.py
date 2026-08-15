"""Prometheus 指标：通过 prometheus-fastapi-instrumentator 暴露 /metrics。"""

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator


def setup_metrics(app: FastAPI) -> None:
    """采集 HTTP 请求数/延迟等默认指标并暴露 /metrics（不进入 OpenAPI 文档）。"""
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
