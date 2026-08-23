"""examples/openai_agent.py 测试：假模型 + 真实网关 fixture 离线跑通完整 Agent 闭环。"""

import json

from examples.gateway_client import GatewayClient, tool_to_function_schema
from examples.openai_agent import MockModel, run_agent


class _DirectAnswerModel:
    """第一轮就给出最终回答的模型：验证不走工具的兜底分支。"""

    async def __call__(self, messages, tools):  # noqa: ANN001, ARG002
        return {"role": "assistant", "content": "无需工具，直接回答"}


async def test_mock_agent_full_loop(gateway_client) -> None:
    """离线假模型 + 真实网关：提问 -> 调 demo_sql__ask -> 回填 -> 最终回答。"""
    gateway = GatewayClient("http://test", "test-key", client=gateway_client)
    tools = await gateway.list_tools()
    functions = [tool_to_function_schema(t) for t in tools]
    answer = await run_agent(
        "有多少客户？", model=MockModel(), gateway=gateway, functions=functions
    )
    assert "SELECT" in answer
    assert "| 5 |" in answer  # 网关真实执行 SQL 后的结果回填


async def test_run_agent_direct_answer(gateway_client) -> None:
    """模型第一轮直接回答时不调用任何工具、不访问网关。"""
    gateway = GatewayClient("http://test", "test-key", client=gateway_client)
    answer = await run_agent("你好", model=_DirectAnswerModel(), gateway=gateway, functions=[])
    assert answer == "无需工具，直接回答"


async def test_mock_model_first_round_tool_call() -> None:
    """第一轮：优先选 preferred_tool 并把提问填进 question 参数。"""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "demo_sql__echo",
                "parameters": {"type": "object", "properties": {"message": {"type": "string"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "demo_sql__ask",
                "parameters": {"type": "object", "properties": {"question": {"type": "string"}}},
            },
        },
    ]
    message = await MockModel()([{"role": "user", "content": "有多少客户？"}], tools)
    call = message["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "demo_sql__ask"
    assert json.loads(call["function"]["arguments"]) == {"question": "有多少客户？"}


async def test_mock_model_final_round_uses_tool_result() -> None:
    """第二轮：看到 tool 消息后把工具结果包装成最终回答。"""
    messages = [
        {"role": "user", "content": "有多少客户？"},
        {"role": "assistant", "tool_calls": []},
        {"role": "tool", "tool_call_id": "call_mock_1", "content": "| 5 |"},
    ]
    message = await MockModel()(messages, [])
    assert message == {
        "role": "assistant",
        "content": "（离线演示·假模型）网关返回的工具结果：\n| 5 |",
    }
