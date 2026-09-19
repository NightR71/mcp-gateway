"""前端（/chat）契约模型：Agent 能力状态探测（M4）。

前端靠它决定「正常对话」还是「维护页」——执行计划 §2 决策 6：简历 Agent 为
real-model-only，**无 Key 或模型不可用显示维护页，绝不用假剧本冒充回答**。
因此这里如实上报模式，由前端（结合 `allow_mock_demo` 配置）决定是否放行演示。
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.admin import PublicMode

# - real：接入真实模型（配了 GATEWAY_AGENT_API_KEY 且非 mock）——对外可用
# - mock：离线假模型（本地/CI 演示用）——默认对外显示维护页，避免假剧本冒充回答
# - disabled：Agent 未启用或未配 Key——维护页
AgentMode = Literal["real", "mock", "disabled"]


class AgentStatus(BaseModel):
    """GET /agent/status 的响应：前端启动时探测一次。"""

    agent_enabled: bool = Field(description="AgentRunner 是否就绪（未启用/未配 Key 为 false）")
    mode: AgentMode = Field(description="real / mock / disabled")
    usable: bool = Field(
        description="前端是否可发起对话：mode=real，或 mode=mock 且服务端允许 mock 演示"
    )
    mock_demo_enabled: bool = Field(
        description="服务端是否允许在 mock 模式下用于演示（config 的 frontend.allow_mock_demo）"
    )
    model_label: str = Field(default="", description="展示用模型标识（不含端点与任何密钥）")
    maintenance_hint: str = Field(default="", description="维护页展示的固定说明文案")
    public_mode: PublicMode = Field(
        default="closed",
        description="三态公开开关的生效值（M6）：只有 public 才对外可问答（诊断用，无敏感信息）",
    )
    preset_questions: list[str] = Field(
        default_factory=list, description="右栏气泡卡预设问题（按引导顺序，点击即送进对话流）"
    )
