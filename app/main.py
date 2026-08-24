"""FastAPI 应用入口：挂路由、横切层、启动/清理事件。"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agent.models import MockModel, build_openai_model
from app.agent.runner import AgentRunner
from app.api.routes import agent, health, servers, tools
from app.config import (
    get_agent_config,
    get_auth_config,
    get_router_config,
    get_server_configs,
    get_settings,
)
from app.core.logging import configure_logging, get_logger
from app.core.metrics import setup_metrics
from app.core.rate_limit import RateLimiter
from app.core.security import SQLiteAPIKeyStore
from app.mcp.registry import ToolRegistry
from app.mcp.tool_router import ToolRouter

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("gateway_started", app_name=settings.app_name, version=settings.version)

    # 协议层：连接所有 MCP Server 并聚合工具
    registry = ToolRegistry(get_server_configs(), tool_call_timeout=settings.tool_call_timeout)
    await registry.connect_all()
    app.state.registry = registry

    # 语义工具路由（阶段 1：GET /tools?query= 关键词过滤 + top-k）
    router_config = get_router_config()
    app.state.router = ToolRouter(top_k=router_config.top_k, min_tools=router_config.min_tools)

    # Agent 能力（阶段 2）：按配置惰性创建，未启用保持 None（路由返回 503）
    agent_runner = _build_agent_runner(registry, app.state.router)
    app.state.agent_runner = agent_runner

    # 横切层：API Key 存储（建表 + 种子）与令牌桶限流器
    auth_config = get_auth_config()
    key_store = SQLiteAPIKeyStore(auth_config.db_path)
    await key_store.init(auth_config.api_keys)
    app.state.key_store = key_store
    app.state.rate_limiter = RateLimiter()

    yield

    if agent_runner is not None:
        await agent_runner.close()
    await registry.close()
    await key_store.close()
    logger.info("gateway_stopped")


def _build_agent_runner(registry: ToolRegistry, router: ToolRouter) -> AgentRunner | None:
    """按 AgentConfig 构造 AgentRunner；未启用/未配 Key 且非 mock 时返回 None。"""
    config = get_agent_config()
    if not config.enabled:
        logger.info("agent_disabled")
        return None
    if config.mock:
        logger.info("agent_ready", mode="mock")
        return AgentRunner(
            registry,
            MockModel(),
            router=router,
            max_rounds=config.max_rounds,
            routing_top_k=config.routing_top_k,
        )
    api_key = os.getenv("GATEWAY_AGENT_API_KEY", "")
    if not api_key:
        logger.warning("agent_no_api_key")
        return None
    model = build_openai_model(config.base_url, api_key, config.model)
    return AgentRunner(
        registry,
        model,
        router=router,
        max_rounds=config.max_rounds,
        routing_top_k=config.routing_top_k,
    )


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
    setup_metrics(app)
    app.include_router(health.router)
    app.include_router(tools.router)
    app.include_router(servers.router)
    app.include_router(agent.router)
    return app


app = create_app()
