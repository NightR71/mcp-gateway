"""/metrics 暴露策略（M1 §5.3）：默认关闭公网暴露，开启后默认要求 API Key。

- metrics.enabled=false（默认）→ 404，与不存在的路由不可区分；
- enabled=true + require_auth=true（默认）→ 复用统一鉴权 + 限流（401/429）；
- enabled=true + require_auth=false → 匿名可读（仅限本机/内网部署自行选择）。
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.deps import authenticate_key, enforce_rate_limit, get_key_store, get_rate_limiter
from app.config import get_metrics_config
from app.core.logging import get_logger
from app.core.metrics import metrics_exposition

router = APIRouter(tags=["metrics"])
logger = get_logger(__name__)


async def _metrics_guard(request: Request) -> None:
    """/metrics 守卫：按配置决定是否鉴权 + 限流（未启用/无需鉴权时直接放行）。

    注意：必须先判断 enabled 再鉴权——disabled 时不能触碰 app.state.key_store
    （未跑 lifespan 的场景没有该状态），保证默认配置下干净地返回 404。
    """
    config = get_metrics_config()
    if not config.enabled or not config.require_auth:
        return
    key_info = await authenticate_key(get_key_store(request), request.headers.get("X-API-Key"))
    await enforce_rate_limit(key_info, get_rate_limiter(request))


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint(_guard: None = Depends(_metrics_guard)) -> Response:
    """Prometheus 指标端点（是否可见/是否需要 Key 由 metrics 配置决定）。"""
    if not get_metrics_config().enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    return metrics_exposition()
