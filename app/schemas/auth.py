"""鉴权相关的 Pydantic 模型。"""

from pydantic import BaseModel, Field


class APIKeyInfo(BaseModel):
    """一个 API Key 的描述：鉴权凭证 + 限流配额 + 租户/工具权限（阶段 6）的载体。"""

    key: str = Field(description="API Key 值（客户端经 X-API-Key 请求头携带）")
    name: str = Field(description="Key 归属方标识（日志/指标维度）")
    rate_limit_per_minute: int = Field(default=60, ge=1, description="每分钟请求上限（令牌桶容量）")
    rate_limit_per_hour: int | None = Field(
        default=None,
        ge=1,
        description="每小时请求上限（小时桶容量，M1）；None = 不启用小时配额（兼容旧数据）",
    )
    tenant: str = Field(default="default", description="租户标识；旧数据/未配置时归入 default")
    allowed_tools: list[str] | None = Field(
        default=None,
        description=(
            "工具白名单（{server}__{tool} 全名）；None = 全部工具可访问"
            "（兼容旧数据，None 与空列表语义不同：None 表示不限制）"
        ),
    )
    agent_allowed: bool | None = Field(
        default=None,
        description=(
            "能否触发网关内 Agent（模型调用，M6）；None = 兼容旧行为（允许），"
            "与 allowed_tools 的 None 语义一致；False = 只能调 MCP 工具，"
            "调 /agent/run* 返回 403——公开演示 Key 即便泄露也零模型成本"
        ),
    )
    is_admin: bool = Field(
        default=False,
        description=(
            "管理员能力位（M6）：保护 /admin/* 管理接口。该字段**不落库、不进 YAML**——"
            "管理员 Key 唯一来源是环境变量 GATEWAY_ADMIN_KEY，由鉴权入口按请求现算"
            "（见 app/core/security.py），换锁不需要迁移数据"
        ),
    )
