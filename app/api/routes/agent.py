"""Agent API（阶段 2/4）：POST /agent/run（同步）+ POST /agent/run/stream（SSE 流式）。

M4：新增 GET /agent/status——供 /chat 前端探测能力状态（决定正常对话或维护页）。
"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agent.schemas import AgentRequest, AgentResponse
from app.api.deps import AgentAccessDep, AgentDep, ModeStoreDep
from app.config import get_agent_config, get_resume_kb_config
from app.core.errors import log_and_hide
from app.core.logging import get_logger
from app.schemas.chat import AgentMode, AgentStatus

router = APIRouter(tags=["agent"])
logger = get_logger(__name__)


@router.get("/agent/status", response_model=AgentStatus)
async def get_agent_status(agent: AgentDep, mode_store: ModeStoreDep) -> AgentStatus:
    """探测 Agent 能力状态（M4，前端启动时调用一次）。

    **不鉴权**：只返回模式与展示用标识，不含端点、凭证或任何内部细节；
    前端据 `usable` 决定「正常对话」还是「维护页」。模式如实上报——
    mock（离线假模型）默认不可用，避免假剧本冒充真实回答（执行计划 §2 决策 6）。
    M6：叠加三态公开开关——`internal`/`closed` 下 `usable` 一律 false（对外维护页），
    并如实上报 `public_mode`，让"维护页是模型没配好还是开关关着"一眼可判。
    """
    config = get_agent_config()
    frontend = get_resume_kb_config().frontend
    public_mode = mode_store.get()
    if agent is None:
        mode: AgentMode = "disabled"
        model_label = ""
    elif config.mock:
        mode = "mock"
        model_label = "离线演示模型"
    else:
        mode = "real"
        model_label = config.model
    available = mode == "real" or (mode == "mock" and frontend.allow_mock_demo)
    return AgentStatus(
        agent_enabled=agent is not None,
        mode=mode,
        usable=available and public_mode == "public",
        mock_demo_enabled=frontend.allow_mock_demo,
        model_label=model_label,
        maintenance_hint=frontend.maintenance_hint,
        preset_questions=list(frontend.preset_questions),
        public_mode=public_mode,
    )


@router.post("/agent/run", response_model=AgentResponse)
async def run_agent(
    body: AgentRequest,
    agent: AgentDep,
    key: AgentAccessDep,
) -> AgentResponse:
    """执行一次 Agent 任务：工具选择（语义路由）→ 工具调用 → 最终回答。

    - 鉴权与限流与工具接口一致（401 / 429）。
    - M1：工具注入与调用均按当前 Key 的白名单收口；内部错误不外泄
      （对外只返回不透明文案 + 关联 ID，完整异常进日志）。
    - M6：`AgentAccessDep` 统一收口三态开关（503）与 Key 的 Agent 能力位（403）。
    - Agent 未启用（agent.enabled=false 或未配 LLM Key 且非 mock）返回 503。
    """
    if agent is None:
        raise _agent_disabled()
    try:
        return await agent.run(body, key=key)
    except Exception as exc:
        detail = log_and_hide("agent_run_failed", exc)
        raise HTTPException(status_code=502, detail=detail) from exc


@router.post("/agent/run/stream")
async def run_agent_stream(
    body: AgentRequest,
    agent: AgentDep,
    key: AgentAccessDep,
) -> StreamingResponse:
    """SSE 流式执行 Agent 任务：步骤实时产出 + 最终回答逐字输出。

    事件序列：`step`（AgentStep）→ `token`（文本增量）→ `step`(final) → `done`（完整响应）。
    鉴权与限流规则与同步端点一致（401 / 429 / 503）；M1：工具注入与调用均按当前 Key
    的白名单收口，失败发 error 事件（JSON detail，对外不透明）。
    M6：与 `/agent/run` 共用同一套收口（三态 503 / 能力位 403）。
    """
    if agent is None:
        raise _agent_disabled()

    async def event_source() -> AsyncIterator[str]:
        try:
            async for event in agent.run_stream(body, key=key):
                yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"
        except Exception as exc:
            detail = log_and_hide("agent_stream_failed", exc)
            payload = json.dumps({"detail": detail}, ensure_ascii=False)
            yield f"event: error\ndata: {payload}\n\n"

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
