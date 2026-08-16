"""FastAPI 应用入口：挂路由、横切层、启动/清理事件。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health, servers, tools
from app.config import get_auth_config, get_server_configs, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.metrics import setup_metrics
from app.core.rate_limit import RateLimiter
from app.core.security import SQLiteAPIKeyStore
from app.mcp.registry import ToolRegistry

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

    # 横切层：API Key 存储（建表 + 种子）与令牌桶限流器
    auth_config = get_auth_config()
    key_store = SQLiteAPIKeyStore(auth_config.db_path)
    await key_store.init(auth_config.api_keys)
    app.state.key_store = key_store
    app.state.rate_limiter = RateLimiter()

    yield

    await registry.close()
    await key_store.close()
    logger.info("gateway_stopped")


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
    setup_metrics(app)
    app.include_router(health.router)
    app.include_router(tools.router)
    app.include_router(servers.router)
    return app


app = create_app()
