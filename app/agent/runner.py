"""AgentRunner（阶段 2/4）：网关内的 Agent 循环宿主（同步 + 流式）。

循环逻辑与 examples/openai_agent.py 验证过的思路一致，但独立实现（双向无依赖）：
- 工具列表先经 ToolRouter 过滤（阶段 1），只注入 top-k
- 工具调用直接走 registry.call_tool()，不走 HTTP 回环
- 每一步记录类型/耗时/结果，返回完整步骤 Trace
- run_stream（阶段 4）：最终回答文本经 chat_stream 逐字透传，步骤实时产出事件

M3（简历 Agent 增量，全部为可选参数，不传时行为与旧版完全一致）：
- `persona`：注入系统提示词（人设/红线），并在输入侧匹配固定回应策略——
  命中红线话题时直接返回配置话术、不进模型（确定性拦截）；
- `output_guard`：对最终回答与步骤内容做 PII 模式打码，流式路径用滑动窗口
  掩码器（跨 token 拼接的 PII 也能完整命中后再打码）。

M6（旁白的结构性抑制）：流式路径**只把"确定不含 tool_calls 的最终轮"文本作为回答下发**，
工具轮（决策轮）的文本一律丢弃——原因与做法见 `ANSWER_REPLAY_CHUNK` 的注释。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from app.agent.models import Message, Model
from app.agent.persona import PolicyHit, ResumePersona
from app.agent.schemas import AgentEvent, AgentRequest, AgentResponse, AgentStep
from app.core.errors import log_and_hide
from app.core.logging import get_logger
from app.core.output_guard import OutputGuard
from app.mcp.registry import ToolRegistry
from app.mcp.schemas import ToolCallTimeoutError, ToolInfo, ToolNotAllowedError, UnknownToolError
from app.mcp.tool_router import ToolRouter
from app.schemas.auth import APIKeyInfo

logger = get_logger(__name__)

MAX_ROUNDS_FALLBACK = "（达到最大往返轮数，模型仍未给出最终回答）"

# 预期内的工具错误（M1 错误脱敏）：这些消息面向调用方、不含内部实现细节，
# 保留可读文案以便模型和用户理解失败原因；其余异常一律不透明化。
EXPECTED_TOOL_ERRORS = (UnknownToolError, ToolNotAllowedError, ToolCallTimeoutError)

# 固定话术的流式分块大小（字符）：给前端打字机效果，又不至于逐字抖动
FIXED_REPLY_CHUNK = 8

# 最终答案的分块回放（M6 结构性修复：决策轮旁白不再下发）：
#
# 背景：OpenAI 兼容的流式协议里，tool_calls 是**静默累积**的（app/agent/models.py 只在
# 轮末的 message 事件里给出），所以"这一轮是不是最终回答轮"只有等轮结束才知道。此前
# 每轮的文本增量都直接作为 token 下发，于是"先检索"那一轮的过渡句（I'll look that up.）
# 被当成回答正文流给了面试官——M5 §6.6 用提示词规则压住过，换模型后复发。
#
# 现在的做法：每轮文本先缓冲，**只有确定不含 tool_calls 的最终轮**才作为回答下发；
# 下发时按块 + 小间隔回放，保留打字机观感（而不是憋到最后一次性弹出）。
# 与模型行为无关，因此换模型/换提示词都不会再漏旁白。
ANSWER_REPLAY_CHUNK = 12  # 每块字符数
ANSWER_REPLAY_DELAY = 0.02  # 块间间隔（秒）→ 约 600 字/秒，接近真人阅读节奏


def tool_to_function_schema(tool: ToolInfo) -> dict[str, Any]:
    """把网关 ToolInfo 转成 OpenAI function calling 的 function 描述。

    网关工具名统一带 `{server}__{tool}` 命名空间前缀，天然满足
    OpenAI 工具命名规则 `^[a-zA-Z0-9_-]+$`（见 app/mcp/schemas.py）。
    """
    input_schema = dict(tool.input_schema)
    input_schema.setdefault("type", "object")
    return {
        "name": tool.name,
        "description": tool.description or tool.name,
        "parameters": input_schema,
    }


def extract_text(result: Any) -> str:
    """从 ToolCallResult 的 content 列表提取纯文本（MCP text content 块）。"""
    parts: list[str] = []
    for item in result.content or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(str(item))
    text = "\n".join(part.strip() for part in parts if part.strip())
    return text or "（工具无文本输出）"


class AgentRunner:
    """Agent 循环执行器：问题 → 工具选择 → 工具调用 → 最终回答 + 步骤 Trace。"""

    def __init__(
        self,
        registry: ToolRegistry,
        model: Model,
        *,
        router: ToolRouter | None = None,
        max_rounds: int = 8,
        routing_top_k: int = 10,
        persona: ResumePersona | None = None,
        output_guard: OutputGuard | None = None,
    ) -> None:
        """构造 Agent 执行器。

        `persona` / `output_guard` 为 M3 简历 Agent 的可选增强：不传时行为与
        旧版完全一致（无系统提示词、无固定回应、无输出打码）。
        """
        self._registry = registry
        self._model = model
        self._router = router
        self._max_rounds = max_rounds
        self._routing_top_k = routing_top_k
        self._persona = persona
        self._output_guard = output_guard

    @property
    def system_prompt(self) -> str:
        """人设系统提示词（未配置人设时为空串 → 不注入 system 消息）。"""
        return self._persona.system_prompt if self._persona is not None else ""

    def _match_policy(self, question: str) -> PolicyHit | None:
        """输入侧固定回应匹配（命中红线话题时跳过模型）。"""
        if self._persona is None:
            return None
        return self._persona.matcher.match(question)

    def _mask(self, text: str) -> str:
        """PII 打码（守门未配置时原样返回）。"""
        if self._output_guard is None:
            return text
        return self._output_guard.mask_text(text)

    def _mask_step(self, step: AgentStep) -> AgentStep:
        """对步骤内容打码（工具结果同样可能带出 PII，属守门范围）。"""
        if self._output_guard is None or not self._output_guard.enabled or not step.content:
            return step
        masked = self._output_guard.mask_text(step.content)
        return step if masked == step.content else step.model_copy(update={"content": masked})

    def _prepare(
        self, request: AgentRequest, key: APIKeyInfo | None = None
    ) -> tuple[int, int, list[dict[str, Any]], list[Message], list[AgentStep]]:
        """同步/流式共用的回合前准备：工具路由 + 注入 + 初始步骤。

        M1：注入前先按 Key 白名单过滤——模型只能「看见」并调用白名单内的工具，
        从源头杜绝经 /agent/run 击穿白名单（执行侧由 registry.call_tool 兜底）。
        M3：配置了人设时在消息最前注入 system 提示词（不进入 steps，避免外发）。
        """
        tools = (
            self._registry.list_tools_for(key) if key is not None else self._registry.list_tools()
        )
        tools_total = len(tools)
        steps: list[AgentStep] = [AgentStep(kind="user", content=request.question)]

        injected = (
            self._router.search(request.question, tools, top_k=self._routing_top_k)
            if self._router is not None
            else tools
        )
        tools_injected = len(injected)
        steps.append(
            AgentStep(kind="tool_select", content=f"注入 {tools_injected}/{tools_total} 个工具")
        )
        functions = [tool_to_function_schema(t) for t in injected]
        tool_entries = [{"type": "function", "function": f} for f in functions]
        messages: list[Message] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": request.question})
        return tools_total, tools_injected, tool_entries, messages, steps

    async def _execute_tool_calls(
        self,
        messages: list[Message],
        tool_calls: list[dict[str, Any]],
        key: APIKeyInfo | None = None,
    ) -> list[AgentStep]:
        """执行一轮模型要求的全部工具调用，产出 tool_call/tool_result 步骤。

        M1：调用带 Key 上下文——白名单校验在 registry.call_tool 收口执行，
        模型幻觉/注入产生的白名单外调用在这里被拒绝并记录为 error 步骤。

        M1 §5.3 错误脱敏：步骤内容会随响应（含 SSE）返回公网客户端，且会回填进
        模型上下文，因此**预期内的工具错误**（未知工具 / 白名单外 / 超时）保留可读
        文案，**其余内部异常**（SDK 报错、连接中断等）一律换成不透明文案 + 关联 ID，
        原文只进日志——与路由层 5xx 用同一套 `log_and_hide`，避免此处成为破窗。
        """
        new_steps: list[AgentStep] = []
        for call in tool_calls:
            function = call.get("function") or {}
            name = str(function.get("name", ""))
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            started = time.perf_counter()
            try:
                result = await self._registry.call_tool(name, arguments, key=key)
                content = extract_text(result)
                is_error = result.is_error
            except EXPECTED_TOOL_ERRORS as exc:  # 预期内：面向调用方的明确错误
                content = f"工具调用失败：{exc}"
                is_error = True
            except Exception as exc:  # 单点失败不中断整个 Agent 循环；内部细节不外泄
                content = log_and_hide("agent_tool_call_failed", exc, tool=name)
                is_error = True
            latency = (time.perf_counter() - started) * 1000
            new_steps.append(
                AgentStep(
                    kind="tool_call",
                    tool=name,
                    content=content if is_error else "",
                    latency_ms=latency,
                    is_error=is_error,
                )
            )
            new_steps.append(
                AgentStep(
                    kind="tool_result",
                    tool=name,
                    content=content,
                    latency_ms=latency,
                    is_error=is_error,
                )
            )
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id", ""), "content": content}
            )
        return new_steps

    async def run(self, request: AgentRequest, *, key: APIKeyInfo | None = None) -> AgentResponse:
        """执行一次 Agent 任务（同步），返回最终回答与完整步骤 Trace。

        `key`：调用方 API Key（M1）——工具注入与调用均按其白名单收口。
        M3：命中固定回应策略时直接返回配置话术（不走模型，确定性拦截）。
        """
        hit = self._match_policy(request.question)
        if hit is not None:
            return self._fixed_reply_response(request.question, hit)

        max_rounds = request.max_rounds or self._max_rounds
        tools_total, tools_injected, tool_entries, messages, steps = self._prepare(request, key)

        rounds = 0
        for rounds in range(1, max_rounds + 1):
            started = time.perf_counter()
            message = await self._model.chat(messages, tool_entries)
            model_latency = (time.perf_counter() - started) * 1000
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                answer = self._mask(str(message.get("content") or "（模型未给出回答）"))
                steps.append(AgentStep(kind="final", content=answer, latency_ms=model_latency))
                return self._response(answer, steps, tools_total, tools_injected, rounds)

            messages.append(message)
            steps.extend(await self._execute_tool_calls(messages, tool_calls, key))

        steps.append(AgentStep(kind="final", content=MAX_ROUNDS_FALLBACK))
        return self._response(MAX_ROUNDS_FALLBACK, steps, tools_total, tools_injected, rounds)

    async def run_stream(
        self, request: AgentRequest, *, key: APIKeyInfo | None = None
    ) -> AsyncIterator[AgentEvent]:
        """流式执行一次 Agent 任务：步骤事件实时产出，最终回答逐字透传。

        事件序列：step*（user/tool_select/...）→ token*（最终回答逐块）
        → step(final) → done(response)。
        `key`：调用方 API Key（M1）——工具注入与调用均按其白名单收口。
        M3：命中固定回应策略时按同样的 SSE 事件序列下发配置话术（不走模型）；
        最终回答逐 token 经输出守门打码后再下发。
        M6：**工具轮（决策轮）的文本不下发**——只有确定不含 tool_calls 的最终轮才作为
        回答回放（见 ANSWER_REPLAY_CHUNK 的注释：这是旁白的结构性抑制，与模型行为无关）。
        """
        hit = self._match_policy(request.question)
        if hit is not None:
            async for event in self._fixed_reply_stream(request.question, hit):
                yield event
            return

        max_rounds = request.max_rounds or self._max_rounds
        tools_total, tools_injected, tool_entries, messages, steps = self._prepare(request, key)
        for step in steps:
            yield AgentEvent(type="step", step=step)

        masker = self._output_guard.new_stream() if self._output_guard is not None else None

        def guard(chunk: str) -> str:
            """流式下发前的守门（未配置守门时原样返回）。"""
            return chunk if masker is None else masker.feed(chunk)

        rounds = 0
        for rounds in range(1, max_rounds + 1):
            started = time.perf_counter()
            final_message: Message | None = None
            round_text: list[str] = []  # 本轮文本先缓冲：轮末才知道它是回答还是旁白
            async for item in self._model.chat_stream(messages, tool_entries):
                if item["type"] == "delta":
                    round_text.append(item["content"])
                elif item["type"] == "message":
                    final_message = item["message"]
            buffered = "".join(round_text)
            if final_message is None:
                final_message = {
                    "role": "assistant",
                    "content": buffered or "（模型未给出回答）",
                }
            model_latency = (time.perf_counter() - started) * 1000
            tool_calls = final_message.get("tool_calls") or []
            if not tool_calls:
                answer_text = buffered or str(final_message.get("content") or "")
                for index in range(0, len(answer_text), ANSWER_REPLAY_CHUNK):
                    safe = guard(answer_text[index : index + ANSWER_REPLAY_CHUNK])
                    if safe:
                        yield AgentEvent(type="token", text=safe)
                    await asyncio.sleep(ANSWER_REPLAY_DELAY)
                tail = masker.flush() if masker is not None else ""
                if tail:
                    yield AgentEvent(type="token", text=tail)
                streamed = masker.masked_full if masker is not None else answer_text
                answer = self._mask(
                    streamed or str(final_message.get("content") or "（模型未给出回答）")
                )
                final_step = AgentStep(kind="final", content=answer, latency_ms=model_latency)
                steps.append(final_step)
                yield AgentEvent(type="step", step=final_step)
                yield AgentEvent(
                    type="done",
                    response=self._response(answer, steps, tools_total, tools_injected, rounds),
                )
                return

            # 工具轮：本轮文本属决策轮旁白，不进回答（只记日志，便于观测模型行为与调人设）
            if buffered.strip():
                logger.info("decision_round_text_dropped", chars=len(buffered), round=rounds)
            messages.append(final_message)
            for step in await self._execute_tool_calls(messages, tool_calls, key):
                steps.append(step)
                yield AgentEvent(type="step", step=step)

        final_step = AgentStep(kind="final", content=MAX_ROUNDS_FALLBACK)
        steps.append(final_step)
        yield AgentEvent(type="step", step=final_step)
        yield AgentEvent(
            type="done",
            response=self._response(
                MAX_ROUNDS_FALLBACK, steps, tools_total, tools_injected, rounds
            ),
        )

    # -- M3：固定回应（红线话题不走模型） ------------------------------------

    def _fixed_reply_response(self, question: str, hit: PolicyHit) -> AgentResponse:
        """同步固定回应：只产出 user + final 两步，工具统计为 0。"""
        answer = self._mask(hit.reply)
        steps = [
            AgentStep(kind="user", content=question),
            AgentStep(kind="final", content=answer),
        ]
        return self._response(answer, steps, 0, 0, 0, policy=hit.policy_id)

    async def _fixed_reply_stream(self, question: str, hit: PolicyHit) -> AsyncIterator[AgentEvent]:
        """流式固定回应：与正常链路相同的事件序列（step → token* → step → done）。"""
        answer = self._mask(hit.reply)
        user_step = AgentStep(kind="user", content=question)
        final_step = AgentStep(kind="final", content=answer)
        steps = [user_step, final_step]
        yield AgentEvent(type="step", step=user_step)
        for index in range(0, len(answer), FIXED_REPLY_CHUNK):
            yield AgentEvent(type="token", text=answer[index : index + FIXED_REPLY_CHUNK])
        yield AgentEvent(type="step", step=final_step)
        yield AgentEvent(
            type="done",
            response=self._response(answer, steps, 0, 0, 0, policy=hit.policy_id),
        )

    def _response(
        self,
        answer: str,
        steps: list[AgentStep],
        tools_total: int,
        tools_injected: int,
        rounds: int,
        *,
        policy: str | None = None,
    ) -> AgentResponse:
        """组装响应：对 answer 与全部步骤内容统一过一遍输出守门。

        `policy` 非空表示本次回答由固定回应策略产出（写进 extra 便于前端区分，
        不影响既有字段语义）。
        """
        extra: dict[str, Any] = {"policy": policy} if policy else {}
        return AgentResponse(
            answer=self._mask(answer),
            steps=[self._mask_step(step) for step in steps],
            tools_total=tools_total,
            tools_injected=tools_injected,
            rounds=rounds,
            extra=extra,
        )

    async def close(self) -> None:
        """关闭模型持有的资源（如 OpenAI 模型自建的 httpx 客户端）。"""
        aclose = getattr(self._model, "aclose", None)
        if callable(aclose):
            await aclose()
