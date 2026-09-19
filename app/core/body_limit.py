"""请求体大小限制中间件（M1 §5.3 入参设界）：限流限次数，这里限单次请求体积。

先缓冲请求体（最多上限+1 字节，超限立即拒绝，内存安全），再以回放 receive 的
方式交给下层应用——即使恶意请求不带 Content-Length（chunked）也无法绕过。
上限读自 Settings（默认 1MB，YAML/环境变量可调），每请求实时取值以兼容测试
（main.py 在测试打配置环境变量之前就已 import，import 期焊死会读不到测试配置）。

**关键不变量**：请求体交付后必须把 receive 委托给真实实现，不得伪造
`http.disconnect`——Starlette 的 StreamingResponse 会并发监听断连，收到断连即
取消整个响应流（uvicorn 实测：SSE 被腰斩，日志 "ASGI callable returned without
completing response"）。委托后断连语义与未装中间件时完全一致。
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings


class BodySizeLimitMiddleware:
    """请求体超过 max_request_body_bytes 时返回 413，其余请求原样透传。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_bytes = get_settings().max_request_body_bytes
        body = b""
        too_large = False
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return  # 客户端已断开，无需响应
            body += message.get("body", b"")
            if len(body) > max_bytes:
                too_large = True
                break  # 不再继续读入，立即拒绝
            if not message.get("more_body"):
                break

        if too_large:
            response = JSONResponse({"detail": "请求体过大"}, status_code=413)
            await response(scope, receive, send)
            return

        sent_body = False

        async def replay_receive() -> Message:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            # 请求体已交付：后续交回真实 receive（真实断连才报断连），
            # 保证下游 StreamingResponse 的断连监听语义与未装中间件时一致。
            return await receive()

        await self.app(scope, replay_receive, send)
