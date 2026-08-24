"""Agent 层 Pydantic 模型：请求 / 步骤 Trace / 响应（阶段 2）。"""

from typing import Any, Literal

from pydantic import BaseModel, Field

# 步骤类型（与阶段 3 工作台时间线一一对应）
AgentStepKind = Literal["user", "tool_select", "tool_call", "tool_result", "final"]


class AgentRequest(BaseModel):
    """POST /agent/run 的入参。"""

    question: str = Field(description="用户问题")
    max_rounds: int | None = Field(
        default=None, description="覆盖配置的最大往返轮数（不传用配置值）"
    )


class AgentStep(BaseModel):
    """Agent 循环中的一步（供时间线渲染与调试）。"""

    kind: AgentStepKind
    content: str = ""
    tool: str | None = Field(default=None, description="涉及的工具名（tool_call/tool_result）")
    latency_ms: float | None = Field(default=None, description="该步耗时（毫秒）")
    is_error: bool = False


class AgentResponse(BaseModel):
    """POST /agent/run 的响应：最终回答 + 完整步骤 Trace。"""

    answer: str
    steps: list[AgentStep] = Field(default_factory=list)
    tools_total: int = 0
    tools_injected: int = 0
    rounds: int = 0
    extra: dict[str, Any] = Field(default_factory=dict, description="预留扩展位")
