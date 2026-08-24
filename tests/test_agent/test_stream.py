"""阶段 4：Agent / 工具流式测试（SSE 事件序列与内容一致性）。

- runner 层：step → token* → done 事件序列、token 拼接等于最终答案、MockModel 全链路
- API 层：/agent/run/stream 与 /tools/{name}/call/stream 事件序列、无 Key 401
"""

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from app.agent.models import MockModel
from app.agent.runner import AgentRunner
from app.agent.schemas import AgentEvent, AgentRequest
from app.config import MCPServerConfig
from app.mcp.registry import ToolRegistry
from app.mcp.tool_router import ToolRouter

AUTH_HEADERS = {"X-API-Key": "test-key"}


def _demo_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="demo_sql",
        transport="inprocess",
        module="servers.demo_sql_server.server:server",
    )


@pytest.fixture
async def registry() -> AsyncIterator[ToolRegistry]:
    r = ToolRegistry((_demo_config(),))
    await r.connect_all()
    yield r
    await r.close()


async def _collect_events(runner: AgentRunner, question: str) -> list[AgentEvent]:
    return [event async for event in runner.run_stream(AgentRequest(question=question))]


async def test_stream_event_sequence(registry: ToolRegistry) -> None:
    """事件序列：step*（user/tool_select/tool_call/tool_result）→ token* → step(final) → done。"""
    runner = AgentRunner(registry, MockModel(), router=ToolRouter(top_k=10, min_tools=2))
    events = await _collect_events(runner, "有多少客户？")

    types = [e.type for e in events]
    assert types[0] == "step"
    assert types[-1] == "done"  # done 恒为最后
    assert "token" in types
    # 顺序：工具相关步骤 → token（最终回答逐字）→ step(final) → done
    first_token = types.index("token")
    final_index = next(
        i for i, e in enumerate(events) if e.type == "step" and e.step.kind == "final"
    )
    assert final_index > first_token
    assert final_index < len(types) - 1

    # 步骤种类完整（user/tool_select/tool_call/tool_result/final）
    step_kinds = [e.step.kind for e in events if e.type == "step"]
    assert step_kinds[0] == "user"
    assert step_kinds[1] == "tool_select"
    assert "tool_call" in step_kinds
    assert "tool_result" in step_kinds
    assert step_kinds[-1] == "final"

    # done 携带完整响应
    done = events[-1]
    assert done.response is not None
    assert done.response.tools_total == 4
    assert done.response.tools_injected == 1
    assert done.response.rounds == 2


async def test_stream_tokens_join_to_answer(registry: ToolRegistry) -> None:
    """token 拼接等于最终答案（与 done.response.answer 一致）。"""
    runner = AgentRunner(registry, MockModel(), router=ToolRouter(top_k=10, min_tools=2))
    events = await _collect_events(runner, "有多少客户？")

    token_text = "".join(e.text or "" for e in events if e.type == "token")
    done = events[-1]
    assert done.response is not None
    assert token_text == done.response.answer
    assert "SELECT COUNT(*)" in token_text
    assert "| 5 |" in token_text


async def test_stream_mock_full_chain(registry: ToolRegistry) -> None:
    """MockModel 全链路流式：工具选择轮无 token，最终回答轮有 token。"""
    runner = AgentRunner(registry, MockModel(), router=ToolRouter(top_k=10, min_tools=2))
    events = await _collect_events(runner, "有多少客户？")

    tool_call_steps = [e for e in events if e.type == "step" and e.step.kind == "tool_call"]
    assert len(tool_call_steps) == 1
    assert tool_call_steps[0].step.tool == "demo_sql__ask"

    token_events = [e for e in events if e.type == "token"]
    assert len(token_events) > 1  # 逐字/逐块输出


async def test_stream_direct_answer(registry: ToolRegistry) -> None:
    """直接回答分支：无工具调用，token 即为最终回答。"""
    runner = AgentRunner(registry, MockModel(direct=True), router=ToolRouter(top_k=10, min_tools=2))
    events = await _collect_events(runner, "你好")
    types = [e.type for e in events]
    assert "token" in types
    step_kinds = [e.step.kind for e in events if e.type == "step"]
    assert "tool_call" not in step_kinds
    done = events[-1]
    assert done.response is not None
    assert done.response.rounds == 1


# ---------- API 层 ----------


async def test_agent_run_stream_api(gateway_client: AsyncClient) -> None:
    """POST /agent/run/stream：SSE 事件流 step/token/done。"""
    async with gateway_client.stream(
        "POST",
        "/agent/run/stream",
        headers=AUTH_HEADERS,
        json={"question": "有多少客户？"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk

    events = _parse_sse(body)
    names = [name for name, _ in events]
    assert "step" in names
    assert "token" in names
    assert "done" in names
    assert names.index("step") < names.index("token") < names.index("done")
    done_payload = events[-1][1]
    assert done_payload["response"]["tools_total"] == 4
    token_text = "".join(payload.get("text", "") for name, payload in events if name == "token")
    assert token_text == done_payload["response"]["answer"]


async def test_agent_run_stream_unauthorized(gateway_client: AsyncClient) -> None:
    """无 Key 调 /agent/run/stream 仍 401。"""
    resp = await gateway_client.post("/agent/run/stream", json={"question": "x"})
    assert resp.status_code == 401


async def test_tool_call_stream_event_sequence(gateway_client: AsyncClient) -> None:
    """POST /tools/{name}/call/stream：start → result → done。"""
    async with gateway_client.stream(
        "POST",
        "/tools/demo_sql__echo/call/stream",
        headers=AUTH_HEADERS,
        json={"arguments": {"message": "hello"}},
    ) as resp:
        assert resp.status_code == 200
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk

    events = _parse_sse(body)
    names = [name for name, _ in events]
    assert names == ["start", "result", "done"]
    assert events[1][1]["content"][0]["text"] == "echo: hello"


async def test_tool_call_stream_unknown_tool_404(gateway_client: AsyncClient) -> None:
    """未知工具在流开始前 404（语义与同步端点一致）。"""
    resp = await gateway_client.post(
        "/tools/no__such_tool/call/stream", headers=AUTH_HEADERS, json={}
    )
    assert resp.status_code == 404


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """把 SSE 文本解析成 (event, data) 列表（data 为 JSON dict）。"""
    import json

    events: list[tuple[str, dict]] = []
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        name = None
        data_lines: list[str] = []
        for line in frame.split("\n"):
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                data_lines.append(line[6:])
        assert name is not None
        events.append((name, json.loads("\n".join(data_lines)) if data_lines else {}))
    return events
