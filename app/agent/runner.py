"""AgentRunner（阶段 2/4）：网关内的 Agent 循环宿主（同步 + 流式）。

循环逻辑与 examples/openai_agent.py 验证过的思路一致，但独立实现（双向无依赖）：
- 工具列表先经 ToolRouter 过滤（阶段 1），只注入 top-k
- 工具调用直接走 registry.call_tool()，不走 HTTP 回环
- 每一步记录类型/耗时/结果，返回完整步骤 Trace
- run_stream（阶段 4）：最终回答文本经 chat_stream 逐字透传，步骤实时产出事件
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from app.agent.models import Message, Model
from app.agent.schemas import AgentEvent, AgentRequest, AgentResponse, AgentStep
from app.mcp.registry import ToolRegistry
from app.mcp.schemas import ToolInfo
from app.mcp.tool_router import ToolRouter

MAX_ROUNDS_FALLBACK = "（达到最大往返轮数，模型仍未给出最终回答）"


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
    ) -> None:
        self._registry = registry
        self._model = model
        self._router = router
        self._max_rounds = max_rounds
        self._routing_top_k = routing_top_k

    def _prepare(
        self, request: AgentRequest
    ) -> tuple[int, int, list[dict[str, Any]], list[Message], list[AgentStep]]:
        """同步/流式共用的回合前准备：工具路由 + 注入 + 初始步骤。"""
        tools = self._registry.list_tools()
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
        messages: list[Message] = [{"role": "user", "content": request.question}]
        return tools_total, tools_injected, tool_entries, messages, steps

    async def _execute_tool_calls(
        self, messages: list[Message], tool_calls: list[dict[str, Any]]
    ) -> list[AgentStep]:
        """执行一轮模型要求的全部工具调用，产出 tool_call/tool_result 步骤。"""
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
                result = await self._registry.call_tool(name, arguments)
                content = extract_text(result)
                is_error = result.is_error
            except Exception as exc:  # 单点失败不中断整个 Agent 循环
                content = f"工具调用失败：{exc}"
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

    async def run(self, request: AgentRequest) -> AgentResponse:
        """执行一次 Agent 任务（同步），返回最终回答与完整步骤 Trace。"""
        max_rounds = request.max_rounds or self._max_rounds
        tools_total, tools_injected, tool_entries, messages, steps = self._prepare(request)

        rounds = 0
        for rounds in range(1, max_rounds + 1):
            started = time.perf_counter()
            message = await self._model.chat(messages, tool_entries)
            model_latency = (time.perf_counter() - started) * 1000
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                answer = str(message.get("content") or "（模型未给出回答）")
                steps.append(AgentStep(kind="final", content=answer, latency_ms=model_latency))
                return self._response(answer, steps, tools_total, tools_injected, rounds)

            messages.append(message)
            steps.extend(await self._execute_tool_calls(messages, tool_calls))

        steps.append(AgentStep(kind="final", content=MAX_ROUNDS_FALLBACK))
        return self._response(MAX_ROUNDS_FALLBACK, steps, tools_total, tools_injected, rounds)

    async def run_stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        """流式执行一次 Agent 任务：步骤事件实时产出，最终回答逐字透传。

        事件序列：step*（user/tool_select/...）→ token*（最终回答逐字）
        → step(final) → done(response)。
        """
        max_rounds = request.max_rounds or self._max_rounds
        tools_total, tools_injected, tool_entries, messages, steps = self._prepare(request)
        for step in steps:
            yield AgentEvent(type="step", step=step)

        rounds = 0
        for rounds in range(1, max_rounds + 1):
            started = time.perf_counter()
            final_message: Message | None = None
            content_parts: list[str] = []
            async for item in self._model.chat_stream(messages, tool_entries):
                if item["type"] == "delta":
                    content_parts.append(item["content"])
                    yield AgentEvent(type="token", text=item["content"])
                elif item["type"] == "message":
                    final_message = item["message"]
            if final_message is None:
                final_message = {
                    "role": "assistant",
                    "content": "".join(content_parts) or "（模型未给出回答）",
                }
            model_latency = (time.perf_counter() - started) * 1000
            tool_calls = final_message.get("tool_calls") or []
            if not tool_calls:
                answer = "".join(content_parts) or str(
                    final_message.get("content") or "（模型未给出回答）"
                )
                final_step = AgentStep(kind="final", content=answer, latency_ms=model_latency)
                steps.append(final_step)
                yield AgentEvent(type="step", step=final_step)
                yield AgentEvent(
                    type="done",
                    response=self._response(answer, steps, tools_total, tools_injected, rounds),
                )
                return

            messages.append(final_message)
            for step in await self._execute_tool_calls(messages, tool_calls):
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

    @staticmethod
    def _response(
        answer: str,
        steps: list[AgentStep],
        tools_total: int,
        tools_injected: int,
        rounds: int,
    ) -> AgentResponse:
        return AgentResponse(
            answer=answer,
            steps=steps,
            tools_total=tools_total,
            tools_injected=tools_injected,
            rounds=rounds,
        )

    async def close(self) -> None:
        """关闭模型持有的资源（如 OpenAI 模型自建的 httpx 客户端）。"""
        aclose = getattr(self._model, "aclose", None)
        if callable(aclose):
            await aclose()
