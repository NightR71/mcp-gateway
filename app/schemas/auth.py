"""鉴权相关的 Pydantic 模型。"""

from pydantic import BaseModel, Field


class APIKeyInfo(BaseModel):
    """一个 API Key 的描述：鉴权凭证 + 限流配额 + 租户/工具权限（阶段 6）的载体。"""

    key: str = Field(description="API Key 值（客户端经 X-API-Key 请求头携带）")
    name: str = Field(description="Key 归属方标识（日志/指标维度）")
    rate_limit_per_minute: int = Field(default=60, ge=1, description="每分钟请求上限（令牌桶容量）")
    tenant: str = Field(default="default", description="租户标识；旧数据/未配置时归入 default")
    allowed_tools: list[str] | None = Field(
        default=None,
        description=(
            "工具白名单（{server}__{tool} 全名）；None = 全部工具可访问"
            "（兼容旧数据，None 与空列表语义不同：None 表示不限制）"
        ),
    )
