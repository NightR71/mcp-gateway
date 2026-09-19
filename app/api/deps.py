"""FastAPI 依赖注入：路由层统一从这里拿配置/注册中心/鉴权/限流等依赖。"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.agent.runner import AgentRunner
from app.config import (
    Settings,
    get_resume_kb_config,
    get_router_config,
    get_settings,
)
from app.core.logging import get_logger
from app.core.mode import ModeStore, build_mode_store
from app.core.rate_limit import RateLimiter
from app.core.security import APIKeyStore, match_admin_key
from app.mcp.registry import ToolRegistry
from app.mcp.tool_router import ToolRouter
from app.schemas.auth import APIKeyInfo

logger = get_logger(__name__)

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_registry(request: Request) -> ToolRegistry:
    """从 app.state 拿工具注册中心（lifespan 启动时已就绪）。"""
    return request.app.state.registry  # type: ignore[no-any-return]


RegistryDep = Annotated[ToolRegistry, Depends(get_registry)]


_default_router: ToolRouter | None = None


def get_router(request: Request) -> ToolRouter:
    """从 app.state 拿语义工具路由（lifespan 启动时已就绪）。

    未挂载（未跑 lifespan 的测试/独立使用场景）时退回默认实例，保证零配置可用。
    """
    router = getattr(request.app.state, "router", None)
    if router is not None:
        return router  # type: ignore[no-any-return]
    global _default_router
    if _default_router is None:
        _default_router = ToolRouter()
    return _default_router


RouterDep = Annotated[ToolRouter, Depends(get_router)]


def router_enabled() -> bool:
    """语义路由是否启用（YAML routing.enabled，进程级缓存）。"""
    return get_router_config().enabled


def get_agent_runner(request: Request) -> AgentRunner | None:
    """从 app.state 拿 AgentRunner（lifespan 按 AgentConfig 惰性创建）。

    未启用（agent.enabled=false 或未配 Key）时为 None，路由返回 503。
    """
    return getattr(request.app.state, "agent_runner", None)  # type: ignore[no-any-return]


AgentDep = Annotated[AgentRunner | None, Depends(get_agent_runner)]


def get_key_store(request: Request) -> APIKeyStore:
    """从 app.state 拿 API Key 存储（lifespan 启动时已初始化）。"""
    return request.app.state.key_store  # type: ignore[no-any-return]


KeyStoreDep = Annotated[APIKeyStore, Depends(get_key_store)]


def get_rate_limiter(request: Request) -> RateLimiter:
    """从 app.state 拿令牌桶限流器。"""
    return request.app.state.rate_limiter  # type: ignore[no-any-return]


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def get_mode_store(request: Request) -> ModeStore:
    """从 app.state 拿三态公开开关（M6，lifespan 按配置构造）。

    未挂载（未跑 lifespan 的测试/独立使用场景）时退回按配置现构造，保证零配置可用
    （与 get_router 的兜底策略一致）。
    """
    store = getattr(request.app.state, "mode_store", None)
    if store is not None:
        return store  # type: ignore[no-any-return]
    return build_mode_store()


ModeStoreDep = Annotated[ModeStore, Depends(get_mode_store)]


async def authenticate_key(store: APIKeyStore, x_api_key: str | None) -> APIKeyInfo:
    """校验 X-API-Key 值：缺失/无效一律 401（get_current_key 与 /metrics 共用）。

    M6：管理员 Key 先于 Key 库匹配——它只存在于环境变量（`GATEWAY_ADMIN_KEY`），
    不进库、不进 YAML；命中即返回管理员身份（配额来自配置的 `auth.admin` 节）。
    顺序放在库查询之前是为了让"管理员 Key 与某个库内 Key 撞值"时以管理员身份处理，
    且不产生额外一次 SQLite 往返。
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="缺少 API Key（请在 X-API-Key 请求头中携带）")
    admin = match_admin_key(x_api_key)
    if admin is not None:
        return admin
    key_info = await store.get(x_api_key)
    if key_info is None:
        logger.warning("api_key_rejected", key_prefix=x_api_key[:8])
        raise HTTPException(status_code=401, detail="无效的 API Key")
    return key_info


async def get_current_key(
    store: KeyStoreDep,
    x_api_key: Annotated[str | None, Header()] = None,
) -> APIKeyInfo:
    """API Key 鉴权：校验 X-API-Key 请求头，缺失/无效一律 401。"""
    return await authenticate_key(store, x_api_key)


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


async def require_admin(key: ProtectedDep) -> APIKeyInfo:
    """管理员守卫（M6）：非管理员一律 403。

    先走 `ProtectedDep`（401 / 429 语义与其他接口一致），再判管理员能力位——
    因此管理接口天然满足项目约定的三个 case（成功 / 401 未授权 / 429 限流）。
    403 的文案不提示"存在管理接口、该怎么申请"，避免给探测者额外信息。
    """
    if not key.is_admin:
        logger.warning("admin_access_denied", key_name=key.name)
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return key


AdminDep = Annotated[APIKeyInfo, Depends(require_admin)]


def maintenance_detail() -> str:
    """对外维护文案（复用前端展示的同一份配置文案，避免两处措辞漂移）。"""
    return get_resume_kb_config().frontend.maintenance_hint


async def enforce_public_access(key: ProtectedDep, store: ModeStoreDep) -> APIKeyInfo:
    """三态公开开关收口（M6 决策 8）：非管理员在 internal/closed 下一律 503。

    判定顺序 = 鉴权（401）→ 限流（429）→ 开关（503）→ 能力位（403，见下）：

    - `public`：放行；
    - `internal`：对外维护，但管理员可自测（放行）；
    - `closed`：一律维护，含管理员——它是"成本应急闸门"，必须能一刀关死。

    503 的 detail 用前端维护页的同一份文案：对外不暴露"开关现在是哪个态"
    （要看态请走免 Key 的 `/agent/status`，那里的语义是"能不能用"，不是"为什么"）。
    """
    mode = store.get()
    if mode == "closed" or (mode == "internal" and not key.is_admin):
        logger.warning("agent_blocked_by_public_mode", key_name=key.name, public_mode=mode)
        raise HTTPException(status_code=503, detail=maintenance_detail())
    return key


PublicAccessDep = Annotated[APIKeyInfo, Depends(enforce_public_access)]


async def require_agent_allowed(key: PublicAccessDep) -> APIKeyInfo:
    """Agent 能力位收口（M6 / 0.5-A）：`agent_allowed=False` 的 Key 只能调 MCP 工具。

    这是"公开 Key 即便泄露也烧不到模型余额"的结构性保证——它执行在 `/agent/run`
    与 `/agent/run/stream` 的唯一入口上，与 M1 把工具白名单收口进
    `registry.call_tool()` 是同一种做法：规则只有一处实现，任何新入口都得过这里。

    顺序放在开关之后：站点级闸门（503）优先于身份级能力（403），
    这样在挂维护页时不会顺带透露"哪些 Key 有模型权限"。
    """
    if key.agent_allowed is False:
        logger.warning("agent_not_allowed_for_key", key_name=key.name)
        raise HTTPException(status_code=403, detail="该 API Key 无权调用 Agent（仅限 MCP 工具）")
    return key


AgentAccessDep = Annotated[APIKeyInfo, Depends(require_agent_allowed)]
