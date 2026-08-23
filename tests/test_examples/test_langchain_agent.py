"""examples/langchain_agent.py 测试：仅依赖 langchain-core（未装 agent 组时自动跳过）。

FakeMessagesListChatModel 模拟 LLM 决策，工具执行仍真实走网关 fixture，
离线跑通 LangChain 版「LLM -> 网关 -> MCP Server」闭环。
"""

import pytest

pytest.importorskip("langchain_core")

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402

from examples.gateway_client import GatewayClient  # noqa: E402
from examples.langchain_agent import run_agent, tool_to_langchain_tool  # noqa: E402


class _RecordingScriptedModel(BaseChatModel):
    """按剧本返回响应，并记录最后一次接收的消息列表。

    第 1 次调用（无 tool 消息）→ 剧本第 0 条（发起工具调用）；
    第 N 次调用（N-1 条 tool 消息）→ 剧本第 N-1 条，可据此断言工具结果回填。
    """

    responses: list[AIMessage]
    last_messages: list[BaseMessage] | None = None

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager=None,  # noqa: ANN001
        **kwargs,  # noqa: ANN003
    ) -> ChatResult:
        tool_count = sum(1 for m in messages if isinstance(m, ToolMessage))
        index = min(tool_count, len(self.responses) - 1)
        self.last_messages = list(messages)
        return ChatResult(generations=[ChatGeneration(message=self.responses[index])])

    @property
    def _llm_type(self) -> str:
        return "recording-scripted-model"


async def test_tools_converted_from_gateway(gateway_client) -> None:
    """网关工具动态包装成 LangChain Tools，执行仍走网关（鉴权 + 真实 SQL）。"""
    gateway = GatewayClient("http://test", "test-key", client=gateway_client)
    tools_info = await gateway.list_tools()
    tools = [tool_to_langchain_tool(t, gateway) for t in tools_info]
    by_name = {t.name: t for t in tools}
    assert {"demo_sql__echo", "demo_sql__ask", "demo_sql__run_sql", "demo_sql__list_tables"} <= set(
        by_name
    )
    result = await by_name["demo_sql__ask"].ainvoke({"question": "有多少客户？"})
    assert "| 5 |" in result


async def test_full_loop_with_fake_model(gateway_client) -> None:
    """剧本假模型：第一轮发起工具调用，第二轮给出最终回答，并验证工具结果真实回填。"""
    gateway = GatewayClient("http://test", "test-key", client=gateway_client)
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "demo_sql__ask",
                    "args": {"question": "有多少客户？"},
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="根据查询结果，共有 5 位客户。"),
    ]
    model = _RecordingScriptedModel(responses=responses)
    answer = await run_agent("有多少客户？", model=model, gateway=gateway)
    assert answer == "根据查询结果，共有 5 位客户。"
    # 第二轮发给模型的消息中必须含网关真实执行 SQL 后的工具结果（证明闭环打通）
    assert model.last_messages is not None
    tool_messages = [m for m in model.last_messages if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert "| 5 |" in str(tool_messages[0].content)
