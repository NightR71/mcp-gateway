"""FastAPI 依赖注入：路由层统一从这里拿配置/注册中心/鉴权/限流等依赖。"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.rate_limit import RateLimiter
from app.core.security import APIKeyStore
from app.mcp.registry import ToolRegistry
from app.schemas.auth import APIKeyInfo

logger = get_logger(__name__)

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_registry(request: Request) -> ToolRegistry:
    """从 app.state 拿工具注册中心（lifespan 启动时已就绪）。"""
    return request.app.state.registry  # type: ignore[no-any-return]


RegistryDep = Annotated[ToolRegistry, Depends(get_registry)]


def get_key_store(request: Request) -> APIKeyStore:
    """从 app.state 拿 API Key 存储（lifespan 启动时已初始化）。"""
    return request.app.state.key_store  # type: ignore[no-any-return]


KeyStoreDep = Annotated[APIKeyStore, Depends(get_key_store)]


def get_rate_limiter(request: Request) -> RateLimiter:
    """从 app.state 拿令牌桶限流器。"""
    return request.app.state.rate_limiter  # type: ignore[no-any-return]


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


async def get_current_key(
    store: KeyStoreDep,
    x_api_key: Annotated[str | None, Header()] = None,
) -> APIKeyInfo:
    """API Key 鉴权：校验 X-API-Key 请求头，缺失/无效一律 401。"""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="缺少 API Key（请在 X-API-Key 请求头中携带）")
    key_info = await store.get(x_api_key)
    if key_info is None:
        logger.warning("api_key_rejected", key_prefix=x_api_key[:8])
        raise HTTPException(status_code=401, detail="无效的 API Key")
    return key_info


CurrentKeyDep = Annotated[APIKeyInfo, Depends(get_current_key)]


async def enforce_rate_limit(key: CurrentKeyDep, limiter: RateLimiterDep) -> APIKeyInfo:
    """令牌桶限流：按 API Key 维度，超限返回 429 + Retry-After。"""
    allowed, retry_after = limiter.check(key)
    if not allowed:
        logger.warning("rate_limit_exceeded", key_name=key.name)
        raise HTTPException(
            status_code=429,
            detail="请求超出限流额度，请稍后重试",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )
    return key


# 受保护接口的统一入口：先鉴权（401），再限流（429）
ProtectedDep = Annotated[APIKeyInfo, Depends(enforce_rate_limit)]
