"""pytest 公共夹具。"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_server_configs, get_settings
from app.main import app


@pytest.fixture(autouse=True)
def reset_config_cache() -> AsyncIterator[None]:
    """每个测试前后清理配置缓存，避免环境污染。"""
    get_settings.cache_clear()
    get_server_configs.cache_clear()
    yield
    get_settings.cache_clear()
    get_server_configs.cache_clear()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """基于 ASGI 的测试客户端，无需起真实服务。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
