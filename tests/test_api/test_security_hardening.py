"""M1 安全前置修复的 API 级回归（对应 docs/交互式个人能力展示系统-执行计划.md §5.3）。

- M1.3：请求体大小上限 → 413；
- M1.4：内部错误信息不外泄 → 502 不透明文案 + 关联 ID（同步 / SSE 两类端点）。

小时桶限流的 API 级用例在 tests/test_api/test_tools.py 与 tests/test_agent/test_api.py；
白名单击穿验收主用例在 tests/test_agent/test_api.py；/metrics 策略在
tests/test_core/test_metrics.py 与 tests/test_health.py。
"""

import json

from httpx import AsyncClient

AUTH_HEADERS = {"X-API-Key": "test-key"}


# ---------- 请求体大小上限（413，M1.3） ----------


async def test_request_body_too_large_413(gateway_client: AsyncClient) -> None:
    """请求体超过 max_request_body_bytes（测试配置 20000B）→ 413。"""
    resp = await gateway_client.post(
        "/tools/demo_sql__echo/call",
        headers=AUTH_HEADERS,
        json={"arguments": {"message": "x" * 21000}},
    )
    assert resp.status_code == 413
    assert "请求体过大" in resp.json()["detail"]


async def test_request_body_within_limit_ok(gateway_client: AsyncClient) -> None:
    """上限内的正常请求不受影响（回归）。"""
    resp = await gateway_client.post(
        "/tools/demo_sql__echo/call",
        headers=AUTH_HEADERS,
        json={"arguments": {"message": "hello"}},
    )
    assert resp.status_code == 200
    assert resp.json()["content"][0]["text"] == "echo: hello"


async def test_body_limit_applies_to_agent_run(gateway_client: AsyncClient) -> None:
    """请求体上限全局生效（/agent/run 同样受保护；体限制先于 schema 校验）。"""
    resp = await gateway_client.post(
        "/agent/run",
        headers=AUTH_HEADERS,
        json={"question": "x" * 21000},
    )
    assert resp.status_code == 413


# ---------- 内部错误信息不外泄（不透明 502 + 关联 ID，M1.4） ----------


async def test_tool_call_internal_error_opaque(gateway_client: AsyncClient, monkeypatch) -> None:
    """同步工具调用：内部异常细节只进日志，对外只有不透明文案 + 关联 ID。"""
    from app.main import app

    registry = app.state.registry

    async def boom(name: str, arguments: dict, key=None) -> None:
        raise RuntimeError("internal secret path /tmp/gateway/trace-42")

    monkeypatch.setattr(registry, "call_tool", boom)
    resp = await gateway_client.post("/tools/demo_sql__ask/call", headers=AUTH_HEADERS, json={})
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "服务内部错误（关联ID: " in detail
    assert "secret" not in resp.text
    assert "/tmp/gateway" not in resp.text


async def test_tool_call_stream_internal_error_opaque(
    gateway_client: AsyncClient, monkeypatch
) -> None:
    """流式工具调用：error 事件同样不透明（超时保留明确提示，由 test_tools 覆盖）。"""
    from app.main import app

    registry = app.state.registry

    async def boom(name: str, arguments: dict, key=None) -> None:
        raise RuntimeError("sdk internal stack leaked")

    monkeypatch.setattr(registry, "call_tool", boom)
    async with gateway_client.stream(
        "POST", "/tools/demo_sql__ask/call/stream", headers=AUTH_HEADERS, json={}
    ) as resp:
        assert resp.status_code == 200
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk

    frames = [f for f in body.split("\n\n") if f.strip()]
    names = [f.split("\n")[0].replace("event: ", "") for f in frames]
    assert names == ["start", "error", "done"]
    assert "服务内部错误（关联ID: " in body
    assert "sdk internal stack" not in body


async def test_agent_run_internal_error_opaque(gateway_client: AsyncClient, monkeypatch) -> None:
    """/agent/run：内部异常对外不透明 + 关联 ID。"""
    from app.main import app

    async def boom(body, key=None):
        raise RuntimeError("db password: hunter2")

    monkeypatch.setattr(app.state.agent_runner, "run", boom)
    resp = await gateway_client.post("/agent/run", headers=AUTH_HEADERS, json={"question": "x"})
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "服务内部错误（关联ID: " in detail
    assert "hunter2" not in resp.text


async def test_agent_stream_internal_error_opaque(gateway_client: AsyncClient, monkeypatch) -> None:
    """/agent/run/stream：error 事件对外不透明（JSON detail，前端可读）。"""
    from app.main import app

    async def boom_stream(body, key=None):
        raise RuntimeError("model api key sk-xxx leaked")
        yield  # pragma: no cover（使本函数成为异步生成器）

    monkeypatch.setattr(app.state.agent_runner, "run_stream", boom_stream)
    async with gateway_client.stream(
        "POST", "/agent/run/stream", headers=AUTH_HEADERS, json={"question": "x"}
    ) as resp:
        assert resp.status_code == 200
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk

    assert "event: error" in body
    error_frame = next(f for f in body.split("\n\n") if f.startswith("event: error"))
    payload = json.loads(error_frame.split("data: ", 1)[1])
    assert "服务内部错误（关联ID: " in payload["detail"]
    assert "sk-xxx" not in body


async def test_agent_tool_failure_internal_text_not_exposed(
    gateway_client: AsyncClient, monkeypatch
) -> None:
    """M1 §5.3 回归：Agent 循环内的工具失败不得把内部异常原文透给客户端。

    背景：路由层脱敏看不到 Agent 循环里被捕获的异常，步骤内容（steps / SSE step
    事件）会直达公网访客，且会回填模型上下文——必须在循环内同样脱敏。
    """
    from app.main import app

    secret = "INTERNAL_SDK_PATH=/opt/secret/gateway.py:42"

    async def boom(name: str, arguments: dict, key=None) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(app.state.registry, "call_tool", boom)

    # 同步端点：整份响应（含 steps）都不含原文，步骤里给出关联 ID
    resp = await gateway_client.post(
        "/agent/run", headers=AUTH_HEADERS, json={"question": "有多少客户？"}
    )
    assert resp.status_code == 200
    assert secret not in resp.text
    error_step = next(s for s in resp.json()["steps"] if s["is_error"])
    assert "服务内部错误（关联ID: " in error_step["content"]

    # SSE 端点：step 事件同样不外泄
    async with gateway_client.stream(
        "POST", "/agent/run/stream", headers=AUTH_HEADERS, json={"question": "有多少客户？"}
    ) as stream_resp:
        body = ""
        async for chunk in stream_resp.aiter_text():
            body += chunk
    assert secret not in body
    assert "服务内部错误（关联ID: " in body
