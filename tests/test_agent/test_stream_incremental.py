"""SSE 增量下发不变量测试（离线、无 Key）：事件必须「产生即下发」，不得积压到流末。

为什么值得单独钉住：前端的打字机效果与「流程图节点随真实 step 点亮」全靠这个不变量。
`AgentRunner.run_stream` 在**调用模型之前**就产出了 `user` / `tool_select` 两步
（`_prepare` → `for step in steps: yield`），所以首字节理应在毫秒级到达客户端。

**为什么不能直接用 httpx 的 ASGITransport 测**：`httpx.ASGITransport` 会把整个响应体
收进 `body_parts` 再一次性返回（它不做流式），所有事件都会被它自己攒到流末——用它测
时序会把「传输层缓冲」误判成「应用层积压」（本文件第一版就踩了这个坑）。因此这里
**直接调用 ASGI app**，自定义 `send` 逐条记录 `http.response.body` 的到达时刻。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.agent.runner import AgentRunner
from app.main import app

VISITOR_KEY = "visitor-key-please-change"

# 模型第一轮「思考」耗时：够长，好让「提前到达」与「积压到流末」区分得清清楚楚
FIRST_ROUND_DELAY = 1.5
# 第二轮逐字吐字间隔（用于验证 token 分批到达，而非末尾一次性到达）
TOKEN_DELAY = 0.02
ANSWER = "答案共八字示例"


class _SlowModel:
    """第一轮先停顿再决定调工具，第二轮逐字吐答案——用于观测事件到达时序。"""

    def __init__(self, *, tool_name: str = "resume_kb__search_knowledge") -> None:
        self._tool_name = tool_name

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        raise NotImplementedError

    async def chat_stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        tool_results = [m for m in messages if m.get("role") == "tool"]
        await asyncio.sleep(FIRST_ROUND_DELAY)
        if not tool_results:
            yield {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_slow_1",
                            "type": "function",
                            "function": {
                                "name": self._tool_name,
                                "arguments": '{"query": "自我介绍"}',
                            },
                        }
                    ],
                },
            }
            return
        for char in ANSWER:
            yield {"type": "delta", "content": char}
            await asyncio.sleep(TOKEN_DELAY)
        yield {"type": "message", "message": {"role": "assistant", "content": ANSWER}}


def _scope() -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/agent/run/stream",
        "raw_path": b"/agent/run/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"x-api-key", VISITOR_KEY.encode()),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 51234),
        "server": ("testserver", 80),
    }


async def _drive_app() -> list[tuple[float, str]]:
    """直接驱动 ASGI app，返回 [(相对毫秒, 该次 body 块的文本)]。"""
    body = json.dumps({"question": "介绍一下你自己"}).encode()
    body_sent = False

    async def receive() -> dict[str, Any]:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        # 请求体交付之后：保持连接（不主动报断连，否则 StreamingResponse 会被取消）
        await asyncio.sleep(30)
        return {"type": "http.disconnect"}

    started = time.perf_counter()
    chunks: list[tuple[float, str]] = []

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            text = bytes(message.get("body", b"")).decode("utf-8", "replace")
            if text:
                chunks.append(((time.perf_counter() - started) * 1000, text))

    await app(_scope(), receive, send)
    return chunks


def _kinds(chunks: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """把 body 块解析成 (毫秒, 事件类型)。"""
    events: list[tuple[float, str]] = []
    for ms, text in chunks:
        for block in text.split("\n\n"):
            line = next((ln for ln in block.splitlines() if ln.startswith("event:")), None)
            if line:
                events.append((ms, line[6:].strip()))
    return events


async def _run_with_slow_model() -> list[tuple[float, str]]:
    """装上慢模型（保留真实 registry / 人设 / 守门），跑一次流式请求。"""
    app.state.agent_runner = AgentRunner(app.state.registry, _SlowModel(), router=app.state.router)
    return _kinds(await _drive_app())


async def test_first_step_is_flushed_before_the_model_returns(resume_kb_client: Any) -> None:
    """`user` / `tool_select` 必须在模型第一轮返回之前就到达客户端。

    事件在模型调用之前就已产出，因此首块必须远早于 FIRST_ROUND_DELAY；
    若应用层把事件积压到流末，首块时间会落在整条流结束附近。
    """
    events = await _run_with_slow_model()
    assert events, "未收到任何事件"
    first_ms, first_kind = events[0]
    assert first_kind == "step", f"首个事件应为 step，实际 {first_kind}"
    assert first_ms < FIRST_ROUND_DELAY * 1000 * 0.5, (
        f"首块耗时 {first_ms:.0f}ms，已接近模型第一轮耗时 "
        f"({FIRST_ROUND_DELAY * 1000:.0f}ms)——应用层存在积压"
    )


async def test_token_events_stream_out_in_batches(resume_kb_client: Any) -> None:
    """token 必须是分批到达（打字机效果），而不是末尾一次性到达。"""
    events = await _run_with_slow_model()
    token_times = [ms for ms, kind in events if kind == "token"]
    assert len(token_times) >= 2, f"token 事件不足（{len(token_times)} 个）：{events}"
    spread = max(token_times) - min(token_times)
    expected_spread = TOKEN_DELAY * 1000
    assert spread >= expected_spread, (
        f"token 事件时间跨度 {spread:.0f}ms < 预期 {expected_spread:.0f}ms——像是被攒到一起下发"
    )


async def test_event_order_and_done_payload(resume_kb_client: Any) -> None:
    """事件顺序 step* → token* → done；done 携带完整回答与步骤 Trace。"""
    app.state.agent_runner = AgentRunner(app.state.registry, _SlowModel(), router=app.state.router)
    chunks = await _drive_app()
    events = _kinds(chunks)
    kinds = [kind for _, kind in events]

    assert kinds[0] == "step"
    assert kinds[-1] == "done"
    assert "token" in kinds
    first_token = kinds.index("token")
    assert all(k == "step" for k in kinds[:first_token]), "token 之前不应出现非 step 事件"

    # done 的负载：完整回答 + 两轮 + 收尾 final 步骤
    done_text = "".join(text for _, text in chunks if '"type":"done"' in text)
    assert ANSWER in done_text, "done 负载缺少完整回答"
    payload = json.loads(done_text.split("data:", 1)[1].split("\n", 1)[0].strip())["response"]
    assert payload["rounds"] == 2
    step_kinds = [step["kind"] for step in payload["steps"]]
    assert "tool_call" in step_kinds and step_kinds[-1] == "final"
