"""请求体大小限制中间件单元测试（M1 §5.3 入参设界）。

核心回归：请求体交付后必须把 receive **委托**给真实实现，不得伪造
`http.disconnect`——Starlette 的 StreamingResponse 会并发监听断连，收到伪造断连
即取消整个响应流（uvicorn 实测：SSE 被腰斩，日志 "ASGI callable returned without
completing response"）。`test_streaming_response_not_truncated` 用「请求体读完后
挂起等待」的 receive 确定性复现该缺陷（httpx 测试因 receive 语义差异漏检）。

上限通过打桩 get_settings 固定为 2000B，测试不依赖环境配置。
"""

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from app.core import body_limit
from app.core.body_limit import BodySizeLimitMiddleware

LIMIT = 2000

SCOPE: Scope = {"type": "http", "method": "POST", "path": "/", "headers": []}


@pytest.fixture(autouse=True)
def fixed_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """把请求体上限固定为 LIMIT（中间件在调用时读 get_settings，打桩即生效）。"""
    monkeypatch.setattr(
        body_limit, "get_settings", lambda: SimpleNamespace(max_request_body_bytes=LIMIT)
    )


class _ChunkedReceive:
    """按段吐请求体的 receive，并记录 tail（请求体读完后）被调用的次数。"""

    def __init__(self, chunks: list[bytes], tail: Message | None = None) -> None:
        self._chunks = list(chunks)
        self._tail = tail
        self.tail_calls = 0

    async def __call__(self) -> Message:
        if self._chunks:
            chunk = self._chunks.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(self._chunks)}
        self.tail_calls += 1
        assert self._tail is not None, "请求体已读完，但 tail 未定义"
        return self._tail


class _BlockingReceive(_ChunkedReceive):
    """请求体读完后**挂起等待**（模拟真实服务端：响应完成前不报断连）。"""

    def __init__(self, chunks: list[bytes]) -> None:
        super().__init__(chunks)
        self._never = asyncio.Event()

    async def __call__(self) -> Message:
        if not self._chunks:
            self.tail_calls += 1
            await self._never.wait()  # 永不 set：等价于「客户端仍在等响应」
        return await super().__call__()


class _SendCollector:
    """收集 ASGI 响应消息。"""

    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def __call__(self, message: Message) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int:
        start = next(m for m in self.messages if m["type"] == "http.response.start")
        return int(start["status"])

    @property
    def body(self) -> bytes:
        return b"".join(
            m.get("body", b"") for m in self.messages if m["type"] == "http.response.body"
        )


async def _noop_app(scope: Scope, receive: Receive, send: Send) -> None:
    """不应被调用的应用（用于超限/断连路径断言）。"""


async def test_within_limit_body_replayed_to_app() -> None:
    """上限内的请求体被完整回放给下层应用（含分段的 chunked 请求）。"""
    seen: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(await receive())
        await PlainTextResponse("ok")(scope, receive, send)

    receive = _ChunkedReceive([b"he", b"llo"], tail={"type": "http.disconnect"})
    send = _SendCollector()
    await BodySizeLimitMiddleware(app)(SCOPE, receive, send)

    assert seen == [{"type": "http.request", "body": b"hello", "more_body": False}]
    assert send.status == 200
    assert send.body == b"ok"


async def test_post_body_receive_delegates_to_real_receive() -> None:
    """关键不变量：请求体交付后，后续 receive 委托真实实现（不伪造断连）。"""
    tail: Message = {"type": "http.request", "body": b"tail", "more_body": False}
    got: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await receive()  # 请求体
        got.append(await receive())  # 请求体之后的调用 → 必须拿到真实 receive 的返回值
        await PlainTextResponse("ok")(scope, receive, send)

    receive = _ChunkedReceive([b"body"], tail=tail)
    await BodySizeLimitMiddleware(app)(SCOPE, receive, _SendCollector())

    assert got == [tail]
    assert got[0]["type"] != "http.disconnect"
    assert receive.tail_calls == 1


async def test_streaming_response_not_truncated() -> None:
    """回归：中间件不得让流式响应被断连监听腰斩（uvicorn 实测缺陷）。

    用「请求体读完后挂起」的 receive 确定性复现：伪造断连会让 Starlette 取消
    响应流，导致事件少于 3 个。
    """

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await receive()  # 先读掉请求体

        async def events() -> AsyncIterator[bytes]:
            for i in range(3):
                await asyncio.sleep(0)  # 制造检查点，让取消有机会落地
                yield f"event-{i}\n".encode()

        await StreamingResponse(events(), media_type="text/event-stream")(scope, receive, send)

    send = _SendCollector()
    await asyncio.wait_for(
        BodySizeLimitMiddleware(app)(SCOPE, _BlockingReceive([b"body"]), send), timeout=5.0
    )

    assert send.body == b"event-0\nevent-1\nevent-2\n"


async def test_oversized_body_rejected_413_without_calling_app() -> None:
    """超限请求体 → 413，且下层应用完全不被调用（超限后立即停止读入）。"""
    send = _SendCollector()
    await BodySizeLimitMiddleware(_noop_app)(SCOPE, _ChunkedReceive([b"x" * (LIMIT + 1)]), send)

    assert send.status == 413
    assert "请求体过大" in send.body.decode()


async def test_oversized_chunked_body_rejected_without_content_length() -> None:
    """无 Content-Length 的分段请求同样受控（累计超限即拒绝）。"""
    chunks = [b"x" * 800 for _ in range(4)]  # 累计 3200B > LIMIT
    send = _SendCollector()
    await BodySizeLimitMiddleware(_noop_app)(SCOPE, _ChunkedReceive(chunks), send)

    assert send.status == 413


async def test_body_exactly_at_limit_passes() -> None:
    """恰好等于上限的请求体放行（边界不误伤）。"""
    payload = b"x" * LIMIT
    seen: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(await receive())
        await PlainTextResponse("ok")(scope, receive, send)

    send = _SendCollector()
    await BodySizeLimitMiddleware(app)(SCOPE, _ChunkedReceive([payload]), send)

    assert seen[0]["body"] == payload
    assert send.status == 200


async def test_limit_read_from_settings_not_hardcoded(monkeypatch: pytest.MonkeyPatch) -> None:
    """上限确实读自 Settings：调大上限后同一请求体不再被拒。"""
    body = b"x" * (LIMIT + 1)
    monkeypatch.setattr(
        body_limit,
        "get_settings",
        lambda: SimpleNamespace(max_request_body_bytes=LIMIT * 10),
    )
    seen: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(await receive())
        await PlainTextResponse("ok")(scope, receive, send)

    send = _SendCollector()
    await BodySizeLimitMiddleware(app)(SCOPE, _ChunkedReceive([body]), send)

    assert seen[0]["body"] == body
    assert send.status == 200


async def test_client_disconnect_during_body_read_returns_silently() -> None:
    """读请求体期间客户端断开：静默返回，不产生响应。"""
    send = _SendCollector()
    await BodySizeLimitMiddleware(_noop_app)(
        SCOPE, _ChunkedReceive([], tail={"type": "http.disconnect"}), send
    )

    assert send.messages == []


async def test_non_http_scope_passes_through() -> None:
    """非 HTTP scope（如 lifespan）原样透传。"""
    forwarded: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        forwarded.append(scope["type"])

    await BodySizeLimitMiddleware(app)({"type": "lifespan"}, _ChunkedReceive([]), _SendCollector())

    assert forwarded == ["lifespan"]
