"""三态公开开关的读写实现（M6 决策 8）：公开 / 内部 / 关闭。

**为什么把"读"和"写"分开**：Vercel 这类 Serverless 是多实例的，每个实例的内存与
`/tmp` 都是独立的，冷启动即丢——在进程内写入开关，会出现"我在 A 实例切了关闭，
面试官却被 B 实例正常服务"，或者在下次冷启动后悄悄回落到开。那是最坏的一类故障：
**你以为已经关掉了成本闸门，其实没有**。

所以：

- `ConfigModeStore`（默认）：只读，值来自配置（`Settings.public_mode`），改值必须
  改配置并重新部署。它在任何部署形态下都是确定性的——这是线上权威路径；
- `ProcessModeStore`：进程内可写，仅当配置显式写 `public_mode_store: process`
  时启用（单进程部署：本机 / docker / 单实例 VPS）。此时管理接口可以即时切换，
  确定性由"只有一个进程"保证。

`/admin/mode` 的写入在只读实现下返回 409 + 明确指引，而不是假装成功——
"确定性边界"必须对调用方可见（详见 docs/M6-部署操作手册.md §3.4）。
"""

from typing import Protocol

from app.config import Settings, get_settings, public_mode_source
from app.schemas.admin import PublicMode

# 只读实现下的写失败文案（管理接口 409 的 detail；含确定性路径指引，无任何凭据）
NOT_WRITABLE_HINT = (
    "当前部署不支持运行时切换：多实例 Serverless 无共享状态，进程内写入会在"
    "冷启动或换实例后失效。请改 GATEWAY_PUBLIC_MODE 环境变量或仓库配置后触发一次新部署。"
)


class ModeNotWritableError(RuntimeError):
    """当前部署形态不支持运行时写入（多实例 Serverless）。"""


class ModeStore(Protocol):
    """三态开关的读写接口（实现见本模块两个类）。"""

    @property
    def writable(self) -> bool: ...

    @property
    def source(self) -> str: ...

    def get(self) -> PublicMode: ...

    def set(self, mode: PublicMode) -> None: ...


class ConfigModeStore:
    """只读实现：值来自配置（env > YAML > 默认），改动必须重新部署。"""

    def __init__(self, mode: PublicMode, source: str) -> None:
        self._mode = mode
        self._source = source

    @property
    def writable(self) -> bool:
        return False

    @property
    def source(self) -> str:
        return self._source

    def get(self) -> PublicMode:
        return self._mode

    def set(self, mode: PublicMode) -> None:
        raise ModeNotWritableError(NOT_WRITABLE_HINT)


class ProcessModeStore:
    """进程内实现：单进程部署下即时生效（本机 / docker / 单实例 VPS）。"""

    def __init__(self, mode: PublicMode) -> None:
        self._mode = mode

    @property
    def writable(self) -> bool:
        return True

    @property
    def source(self) -> str:
        return "process"

    def get(self) -> PublicMode:
        return self._mode

    def set(self, mode: PublicMode) -> None:
        self._mode = mode


def build_mode_store(settings: Settings | None = None) -> ModeStore:
    """按配置构造开关存储（默认只读，fail-closed 的部署形态）。"""
    settings = settings if settings is not None else get_settings()
    if settings.public_mode_store == "process":
        return ProcessModeStore(settings.public_mode)
    return ConfigModeStore(settings.public_mode, public_mode_source())
