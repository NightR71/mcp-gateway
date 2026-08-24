"""Agent API（阶段 2/4）：POST /agent/run（同步）+ POST /agent/run/stream（SSE 流式）。"""

from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

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
        raise _agent_disabled()
    try:
        return await agent.run(body)
    except Exception as exc:
        logger.error("agent_run_failed", error=str(exc))
        raise HTTPException(status_code=502, detail=f"Agent 执行失败: {exc}") from exc


@router.post("/agent/run/stream")
async def run_agent_stream(
    body: AgentRequest,
    agent: AgentDep,
    _key: ProtectedDep,
) -> StreamingResponse:
    """SSE 流式执行 Agent 任务：步骤实时产出 + 最终回答逐字输出。

    事件序列：`step`（AgentStep）→ `token`（文本增量）→ `step`(final) → `done`（完整响应）。
    鉴权与限流规则与同步端点一致（401 / 429 / 503）。
    """
    if agent is None:
        raise _agent_disabled()

    async def event_source() -> AsyncIterator[str]:
        try:
            async for event in agent.run_stream(body):
                yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"
        except Exception as exc:
            logger.error("agent_stream_failed", error=str(exc))
            yield f"event: error\ndata: {exc}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


def _agent_disabled() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=(
            "Agent 未启用：请在 config/gateway.yaml 设置 agent.enabled=true；"
            "离线演示可同时设置 mock=true（无需 LLM Key）；"
            "真实模型需设置环境变量 GATEWAY_AGENT_API_KEY"
        ),
    )
