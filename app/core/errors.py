"""对外错误呈现（M1 §5.3）：内部异常细节只进日志，对外只给不透明文案 + 关联 ID。

内部路径、SDK 报错原文等不得返回给公网客户端；凭响应中的 `关联ID` 可在
服务端日志中精确定位对应记录（日志同时携带 error / error_type / error_id）。

放在 core 层（横切关注点）是为了让接口层（routes）与 Agent 层（runner）共用同一套
措辞——两处若各写一份，就会出现「路由脱敏、Agent 步骤漏出原文」的破窗。
"""

import uuid
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


def log_and_hide(event: str, exc: Exception, /, **context: Any) -> str:
    """完整记录内部异常（带关联 ID）后，返回对外的不透明错误文案。

    各 5xx / 步骤失败路径统一走这里，保证措辞一致：`服务内部错误（关联ID: xxxx）`。
    """
    error_id = uuid.uuid4().hex[:12]
    logger.error(
        event,
        error=str(exc),
        error_type=type(exc).__name__,
        error_id=error_id,
        **context,
    )
    return f"服务内部错误（关联ID: {error_id}）"
