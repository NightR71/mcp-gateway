"""管理接口契约模型（M6 决策 8）：三态公开开关。

`PublicMode` 定义在这里而不是 core 层：`config.py`（设置项）与 `core/mode.py`
（读写实现）都要用它，放在 schemas 层可避免 config ↔ core 的循环依赖
（与 `APIKeyInfo` 由 config.py 引用的既有做法一致）。

三态语义（v1 口径，见 docs/M6-部署操作手册.md §3.4）：
- `public`：访客可用（受白名单与限流）；
- `internal`：对外显示维护页、管理员 Key 仍可自测；
- `closed`：一律维护页（含管理员）——成本应急闸门。
"""

from typing import Literal

from pydantic import BaseModel, Field

PublicMode = Literal["public", "internal", "closed"]

# 生效来源（供 /admin/mode 如实上报，避免"我以为它读的是环境变量"这类误判）
ModeSource = Literal["env", "yaml", "default", "process"]


class AdminModeState(BaseModel):
    """GET /admin/mode 的响应：当前生效值 + 来源 + 可写性。"""

    mode: PublicMode = Field(description="当前生效的公开态")
    source: ModeSource = Field(description="生效来源：env / yaml / default / process")
    writable: bool = Field(
        description="当前进程是否支持运行时写入（多实例 Serverless 为 false，写入必 409）"
    )
    note: str = Field(default="", description="可写性与切换方式的说明（不含任何凭据）")


class AdminModeUpdate(BaseModel):
    """POST /admin/mode 的请求体。"""

    mode: PublicMode = Field(description="目标态：public / internal / closed")
