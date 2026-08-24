"""examples/gateway_client.py 的测试：对真实 gateway fixture 跑通 REST 契约。"""

import re

import httpx
import pytest

from examples.gateway_client import GatewayClient, extract_text, tool_to_function_schema


def test_tool_to_function_schema_matches_openai_naming() -> None:
    tool = {
        "name": "demo_sql__ask",
        "description": "用中文自然语言提问",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "问题"}},
            "required": ["question"],
        },
    }
    function = tool_to_function_schema(tool)
    assert function["name"] == "demo_sql__ask"
    assert function["parameters"]["type"] == "object"
    assert function["parameters"]["properties"]["question"]["type"] == "string"
    # 命名空间工具名必须满足 OpenAI / LangChain 工具命名规则 ^[a-zA-Z0-9_-]+$
    assert re.fullmatch(r"[a-zA-Z0-9_-]+", function["name"]) is not None


async def test_gateway_client_list_and_call(gateway_client) -> None:
    """真实网关 fixture：列工具 + 调 demo_sql__ask 全链路。"""
    gateway = GatewayClient("http://test", "test-key", client=gateway_client)
    tools = await gateway.list_tools()
    names = {t["name"] for t in tools}
    assert {
        "demo_sql__echo",
        "demo_sql__ask",
        "demo_sql__run_sql",
        "demo_sql__list_tables",
    } <= names
    result = await gateway.call_tool("demo_sql__ask", {"question": "有多少客户？"})
    assert result["is_error"] is False
    text = extract_text(result)
    assert "SELECT" in text
    assert "| 5 |" in text  # demo 库 5 位客户


async def test_gateway_client_list_tools_with_query(gateway_client) -> None:
    """阶段 1：list_tools(query=...) 透传网关语义路由，只返回相关工具。"""
    gateway = GatewayClient("http://test", "test-key", client=gateway_client)
    tools = await gateway.list_tools(query="销售额", top_k=2)
    names = [t["name"] for t in tools]
    assert len(tools) <= 2
    assert names[0] == "demo_sql__ask"


async def test_gateway_client_unauthorized(gateway_client) -> None:
    """错误 Key 走网关鉴权：401 透传为 HTTPStatusError。"""
    gateway = GatewayClient("http://test", "wrong-key", client=gateway_client)
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await gateway.list_tools()
    assert excinfo.value.response.status_code == 401


async def test_gateway_client_rate_limited(gateway_client) -> None:
    """limited-key 配额 2 次/分钟：第 3 次请求 429（带 Retry-After）。"""
    gateway = GatewayClient("http://test", "limited-key", client=gateway_client)
    await gateway.list_tools()
    await gateway.list_tools()
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await gateway.list_tools()
    assert excinfo.value.response.status_code == 429
