"""pytest 公共夹具。"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.config import (
    get_agent_config,
    get_auth_config,
    get_metrics_config,
    get_resume_kb_config,
    get_router_config,
    get_server_configs,
    get_settings,
)
from app.main import app

TEST_CONFIG_FILE = Path(__file__).parent / "fixtures" / "gateway_test.yaml"
TEST_RESUME_KB_CONFIG_FILE = Path(__file__).parent / "fixtures" / "resume_kb_test.yaml"
TEST_RESUME_KB_SERVER_CONFIG_FILE = (
    Path(__file__).parent / "fixtures" / "gateway_resume_kb_test.yaml"
)


@pytest.fixture(autouse=True)
def reset_config_cache(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """每个测试前后清理配置缓存，避免环境污染。

    autouse 同时把简历 Agent 应用层配置指向测试夹具——否则会读到生产
    `config/resume_kb.yaml`，让所有 Agent 测试意外带上完整人设与红线文案。
    """
    monkeypatch.setenv("RESUME_KB_CONFIG_FILE", str(TEST_RESUME_KB_CONFIG_FILE))
    for getter in (
        get_settings,
        get_server_configs,
        get_auth_config,
        get_router_config,
        get_agent_config,
        get_metrics_config,
        get_resume_kb_config,
    ):
        getter.cache_clear()
    yield
    for getter in (
        get_settings,
        get_server_configs,
        get_auth_config,
        get_router_config,
        get_agent_config,
        get_metrics_config,
        get_resume_kb_config,
    ):
        getter.cache_clear()


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


@pytest.fixture
async def resume_kb_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """M3 简历知识库客户端：只接入 resume_kb server（工具总数 4），含访客 Key。

    配置见 tests/fixtures/gateway_resume_kb_test.yaml
    （visitor-key-please-change / visitor-limited-key / no-kb-key）。
    """
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(TEST_RESUME_KB_SERVER_CONFIG_FILE))
    get_server_configs.cache_clear()
    get_auth_config.cache_clear()
    async with LifespanManager(app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as c:
            yield c
