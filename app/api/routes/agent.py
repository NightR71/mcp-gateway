"""Agent API：POST /agent/run —— 问题 → Agent 循环 → 最终回答 + 步骤 Trace（阶段 2）。"""

from fastapi import APIRouter, HTTPException

from app.agent.schemas import AgentRequest, AgentResponse
from app.api.deps import AgentDep, ProtectedDep
from app.core.logging import get_logger

router = APIRouter(tags=["agent"])
logger = get_logger(__name__)


@router.post("/agent/run", response_model=AgentResponse)
async def run_agent(
    body: AgentRequest,
    agent: AgentDep,
    _key: ProtectedDep,
) -> AgentResponse:
    """执行一次 Agent 任务：工具选择（语义路由）→ 工具调用 → 最终回答。

    - 鉴权与限流与工具接口一致（401 / 429）。
    - Agent 未启用（agent.enabled=false 或未配 LLM Key 且非 mock）返回 503。
    """
    if agent is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Agent 未启用：请在 config/gateway.yaml 设置 agent.enabled=true；"
                "离线演示可同时设置 mock=true（无需 LLM Key）；"
                "真实模型需设置环境变量 GATEWAY_AGENT_API_KEY"
            ),
        )
    try:
        return await agent.run(body)
    except Exception as exc:
        logger.error("agent_run_failed", error=str(exc))
        raise HTTPException(status_code=502, detail=f"Agent 执行失败: {exc}") from exc
