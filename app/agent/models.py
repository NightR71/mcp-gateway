"""Agent 的 LLM 模型抽象（阶段 2）：Model Protocol + OpenAI 兼容工厂 + MockModel。

与 examples/ 的模型实现保持「双向无依赖」：网关内部独立实现，不从 examples 导入。
"""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

# 与 OpenAI function calling 兼容的消息/工具描述类型
Message = dict[str, Any]
FunctionSchema = dict[str, Any]


class Model(Protocol):
    """模型接口：一轮 chat，返回 assistant message（可能含 tool_calls）。"""

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> Message: ...

    async def chat_stream(self, messages: list[Message], tools: list[dict[str, Any]]) -> Any:
        """（可选）流式一轮 chat，yield 事件 dict：

        - {"type": "delta", "content": str} —— 文本增量
        - {"type": "message", "message": Message} —— 本轮完整 assistant message
          （message 可能含 tool_calls；流式文本已并入 message.content）
        """
        raise NotImplementedError


def build_openai_model(
    base_url: str,
    api_key: str,
    model_name: str,
    client: httpx.AsyncClient | None = None,
    *,
    connect_retries: int = 0,
) -> Model:
    """构造走 OpenAI 兼容 chat/completions 的模型。

    base_url 可指向任意兼容端点（One-API / Ollama / DeepSeek 等），模型无关。
    client 缺省时自建 httpx.AsyncClient（随模型 aclose() 关闭）。

    `connect_retries`：传输层连接重试次数（M5 实测必需，见 AgentConfig 注释）。
    重试由 httpx 在**连接层**完成（ConnectError/ConnectTimeout），会重新建立连接，
    因此可绕过「部分后端 IP 的 TLS 证书链校验失败」这类单连接故障；每次尝试仍然
    完整校验证书，不降低安全性。默认 0 = 与旧行为完全一致（测试/自定义 client 不受影响）。
    """

    url = f"{base_url.rstrip('/')}/chat/completions"

    class _OpenAIModel:
        def __init__(self) -> None:
            self._client = client or httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(retries=connect_retries),
                timeout=120.0,
            )
            self._owns_client = client is None

        async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> Message:
            resp = await self._client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_name,
                    "messages": messages,
                    "tools": tools,
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]

        async def chat_stream(self, messages: list[Message], tools: list[dict[str, Any]]) -> Any:
            """OpenAI SSE 流式：yield delta 文本增量，最后 yield 完整 message。

            tool_calls 增量按 index 累积（部分端点把参数拆成多段）。
            """
            async with self._client.stream(
                "POST",
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_name,
                    "messages": messages,
                    "tools": tools,
                    "temperature": 0,
                    "stream": True,
                },
            ) as resp:
                resp.raise_for_status()
                content_parts: list[str] = []
                tool_calls: dict[int, dict[str, Any]] = {}
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                    if delta.get("content"):
                        content_parts.append(delta["content"])
                        yield {"type": "delta", "content": delta["content"]}
                    for call in delta.get("tool_calls") or []:
                        index = int(call.get("index", 0))
                        slot = tool_calls.setdefault(
                            index, {"id": "", "function": {"name": "", "arguments": ""}}
                        )
                        slot["id"] = slot["id"] or call.get("id", "")
                        function = call.get("function") or {}
                        slot["function"]["name"] = slot["function"]["name"] or function.get(
                            "name", ""
                        )
                        slot["function"]["arguments"] += function.get("arguments", "")
                message: Message = {"role": "assistant"}
                if tool_calls:
                    message["tool_calls"] = [
                        {
                            "id": slot["id"],
                            "type": "function",
                            "function": slot["function"],
                        }
                        for slot in tool_calls.values()
                    ]
                if content_parts:
                    message["content"] = "".join(content_parts)
                yield {"type": "message", "message": message}

        async def aclose(self) -> None:
            if self._owns_client:
                await self._client.aclose()

    return _OpenAIModel()


class MockModel:
    """离线演示用假模型：第一轮调用一个网关工具，第二轮把结果包装成最终回答。

    不需要任何 LLM Key 即可完整演示「Agent → 网关 → MCP Server」闭环，
    也是测试的确定性实现（tests/test_agent/）。
    """

    def __init__(
        self,
        *,
        preferred_tool: str = "demo_sql__ask",
        call_id: str = "call_mock_1",
        direct: bool = False,
    ) -> None:
        self.preferred_tool = preferred_tool
        self.call_id = call_id
        self.direct = direct  # True 时第一轮直接给出最终回答（测直接回答分支）

    def _choose_tool(self, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
        """优先选 preferred_tool，其次选名称以 __ask 结尾的工具，最后选第一个。"""
        functions = [t["function"] for t in tools]
        if not functions:
            return None
        for function in functions:
            if function["name"] == self.preferred_tool:
                return function
        for function in functions:
            if function["name"].endswith("__ask"):
                return function
        return functions[0]

    @staticmethod
    def _build_arguments(function: dict[str, Any], question: str) -> dict[str, Any]:
        """把用户提问填进工具 schema 的第一个「问题类」参数。"""
        properties = (function.get("parameters") or {}).get("properties") or {}
        if not properties:
            return {}
        preferred = ("question", "message", "sql", "text", "input")
        param = next((k for k in preferred if k in properties), next(iter(properties)))
        return {param: question}

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> Message:
        if self.direct:
            return {"role": "assistant", "content": "（mock 直接回答）演示库有 5 位客户。"}
        tool_results = [m for m in messages if m.get("role") == "tool"]
        if tool_results:
            content = tool_results[-1].get("content", "")
            return {
                "role": "assistant",
                "content": f"（离线演示·假模型）网关返回的工具结果：\n{content}",
            }
        function = self._choose_tool(tools)
        if function is None:
            return {
                "role": "assistant",
                "content": "（离线演示·假模型）网关没有可用工具，无法演示工具调用。",
            }
        question = str(messages[-1].get("content", ""))
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": self.call_id,
                    "type": "function",
                    "function": {
                        "name": function["name"],
                        "arguments": json.dumps(
                            self._build_arguments(function, question), ensure_ascii=False
                        ),
                    },
                }
            ],
        }

    async def chat_stream(self, messages: list[Message], tools: list[dict[str, Any]]) -> Any:
        """假模型流式：文本按「字」切块逐个 yield，最后 yield 完整 message。

        与 chat() 决策一致（工具选择轮只 yield message 事件，无文本增量）。
        """
        message = await self.chat(messages, tools)
        if not message.get("tool_calls"):
            content = str(message.get("content") or "")
            for char in content:
                yield {"type": "delta", "content": char}
            message["content"] = content
        yield {"type": "message", "message": message}
