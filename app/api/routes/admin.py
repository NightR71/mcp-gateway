"""/admin/* 管理接口（M6 决策 8）：三态公开开关。

两件事：**读**（当前态 + 生效来源 + 是否可写）与**写**（仅单进程部署可写）。
设计取舍（详见 app/core/mode.py 与 docs/M6-部署操作手册.md §3.4）：

- 管理员身份来自环境变量 `GATEWAY_ADMIN_KEY`（不进库、不进 YAML），
  非管理员一律 403；接口本身的 401/429 语义与其他受保护接口完全一致；
- 写入在只读部署（多实例 Serverless）上返回 **409 + 确定性切换指引**，
  而不是假装成功——三种态都能"切换"，但线上确定性切换的手段是"改配置 + 一次部署"；
- 审计日志记录 actor / 变更前后 / 结果，**从不记录 Key 值**。
"""

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminDep, ModeStoreDep
from app.core.logging import get_logger
from app.core.mode import NOT_WRITABLE_HINT, ModeNotWritableError
from app.schemas.admin import AdminModeState, AdminModeUpdate

router = APIRouter(tags=["admin"])
logger = get_logger(__name__)

_WRITABLE_NOTE = "当前为单进程部署：写入立即生效（进程重启会回落到配置值）。"
_READONLY_NOTE = NOT_WRITABLE_HINT


def _state(mode: str, source: str, writable: bool) -> AdminModeState:
    return AdminModeState(
        mode=mode,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        writable=writable,
        note=_WRITABLE_NOTE if writable else _READONLY_NOTE,
    )


@router.get("/admin/mode", response_model=AdminModeState)
async def get_public_mode(key: AdminDep, store: ModeStoreDep) -> AdminModeState:
    """读取三态开关的生效值、来源与可写性（需管理员 Key）。"""
    logger.info(
        "admin_mode_read",
        actor=key.name,
        public_mode=store.get(),
        source=store.source,
        writable=store.writable,
    )
    return _state(store.get(), store.source, store.writable)


@router.post("/admin/mode", response_model=AdminModeState)
async def set_public_mode(
    body: AdminModeUpdate, key: AdminDep, store: ModeStoreDep
) -> AdminModeState:
    """切换三态开关（需管理员 Key）；只读部署返回 409 + 指引。"""
    previous = store.get()
    try:
        store.set(body.mode)
    except ModeNotWritableError as exc:
        logger.warning(
            "admin_mode_write_rejected",
            actor=key.name,
            mode_from=previous,
            mode_to=body.mode,
            reason="store_not_writable",
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info(
        "admin_mode_changed",
        actor=key.name,
        mode_from=previous,
        mode_to=body.mode,
        source=store.source,
    )
    return _state(store.get(), store.source, store.writable)
