"""pytest 公共夹具。"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.config import get_auth_config, get_server_configs, get_settings
from app.main import app

TEST_CONFIG_FILE = Path(__file__).parent / "fixtures" / "gateway_test.yaml"


@pytest.fixture(autouse=True)
def reset_config_cache() -> AsyncIterator[None]:
    """每个测试前后清理配置缓存，避免环境污染。"""
    get_settings.cache_clear()
    get_server_configs.cache_clear()
    get_auth_config.cache_clear()
    yield
    get_settings.cache_clear()
    get_server_configs.cache_clear()
    get_auth_config.cache_clear()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """基于 ASGI 的测试客户端（不跑 lifespan，供无需鉴权的接口用）。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def gateway_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """跑完整 lifespan 的客户端：连 demo server、初始化 Key 存储与限流器。

    配置见 tests/fixtures/gateway_test.yaml（test-key / limited-key 两个测试 Key）。
    """
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(TEST_CONFIG_FILE))
    get_server_configs.cache_clear()
    get_auth_config.cache_clear()
    async with LifespanManager(app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as c:
            yield c
