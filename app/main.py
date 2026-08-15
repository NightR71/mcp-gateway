"""FastAPI 应用入口：挂路由、横切层、启动/清理事件。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health
from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.metrics import setup_metrics

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("gateway_started", app_name=settings.app_name, version=settings.version)
    yield
    logger.info("gateway_stopped")


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
    setup_metrics(app)
    app.include_router(health.router)
    return app


app = create_app()
