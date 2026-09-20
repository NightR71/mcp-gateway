"""成本护栏 `max_tokens` 的接线测试（不联网、不花钱、不碰 Key）。

背景（2026-09-20 反测 A-02，阻断项）：执行计划 §8.3 与《面试题目.md》第 18/58 题都宣称
「`max_rounds`/`max_tokens` 双上限」，但代码里 `max_tokens` **零实现**——输出长度完全交给
提供方默认值。本文件把这句宣称钉到代码上：设了值就必须出现在请求体里，没设就不能出现。

三条不变式，各自对应一个真实的踩坑面：

1. **默认不下发**（`None`）：请求体与旧版逐字一致——离线 mock 演示与 CI 都不受影响，
   也不需要重新跑一遍线上冒烟来确认"没变化"。
2. **两条路径都要带**：线上 `/chat` 走的是 `chat_stream()`（SSE 流式），
   只在 `chat()` 里加护栏等于线上没有护栏。这是最容易漏、也最容易被面试官问出来的一处。
3. **必须显式写 YAML**：`agent` 节只从 YAML 读取（M5 已知坑），代码默认值不会自己生效——
   所以"配置能读进来"这件事要单独测，否则线上静默无护栏而没人发现。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.agent.models import build_openai_model

MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": "介绍下你的网关项目"}]
CALL_LIMIT = 1024

# 一份最小可用的 SSE 响应（chat_stream 只认 data: 行，最后以 [DONE] 收尾）
SSE_BODY = (
    b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    b"data: [DONE]\n\n"
)


def _capturing_client(
    calls: list[httpx.Request], *, body: bytes | None = None
) -> httpx.AsyncClient:
    """MockTransport 客户端：把请求原样记下来，返回一份最小合法响应。"""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if body is None:
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant"}}]})
        return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _sent_body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


def _build(client: httpx.AsyncClient, **kwargs: Any):
    return build_openai_model(
        "https://example.invalid/v1", "fake-key", "fake-model", client, **kwargs
    )


# ---------------------------------------------------------------------------
# 1. 默认（None）＝ 不下发该字段，与旧行为逐字一致
# ---------------------------------------------------------------------------


async def test_omits_max_tokens_by_default_in_chat() -> None:
    """没设 max_tokens ⇒ 非流式请求体里不能有该键（旧行为逐字一致）。"""
    calls: list[httpx.Request] = []
    client = _capturing_client(calls)
    model = _build(client)
    try:
        await model.chat(MESSAGES, [])
    finally:
        await client.aclose()
    assert "max_tokens" not in _sent_body(calls[0]), _sent_body(calls[0])


async def test_omits_max_tokens_by_default_in_stream() -> None:
    """没设 max_tokens ⇒ 流式请求体里同样不能有该键。"""
    calls: list[httpx.Request] = []
    client = _capturing_client(calls, body=SSE_BODY)
    model = _build(client)
    try:
        async for _ in model.chat_stream(MESSAGES, []):
            pass
    finally:
        await client.aclose()
    assert "max_tokens" not in _sent_body(calls[0]), _sent_body(calls[0])


# ---------------------------------------------------------------------------
# 2. 设了值 ⇒ 两条路径都出现在请求体里（线上走的是流式那条）
# ---------------------------------------------------------------------------


async def test_max_tokens_reaches_chat_request_body() -> None:
    calls: list[httpx.Request] = []
    client = _capturing_client(calls)
    model = _build(client, max_tokens=CALL_LIMIT)
    try:
        await model.chat(MESSAGES, [])
    finally:
        await client.aclose()
    body = _sent_body(calls[0])
    assert body["max_tokens"] == CALL_LIMIT
    # 其它字段不受影响（护栏是"增量"，不是替换请求体）
    assert body["model"] == "fake-model" and body["temperature"] == 0


async def test_max_tokens_reaches_stream_request_body() -> None:
    """**线上路径**：`/chat` 走 chat_stream，护栏必须落在这里。"""
    calls: list[httpx.Request] = []
    client = _capturing_client(calls, body=SSE_BODY)
    model = _build(client, max_tokens=CALL_LIMIT)
    try:
        async for _ in model.chat_stream(MESSAGES, []):
            pass
    finally:
        await client.aclose()
    body = _sent_body(calls[0])
    assert body["max_tokens"] == CALL_LIMIT
    assert body["stream"] is True


# ---------------------------------------------------------------------------
# 3. 配置来源：默认 None；显式写进 YAML 才生效
# ---------------------------------------------------------------------------


def test_agent_config_max_tokens_defaults_to_none() -> None:
    """代码默认 None＝不带该字段（离线 mock 与 CI 行为不变），护栏只在显式配置时生效。"""
    from app.config import AgentConfig

    assert AgentConfig().max_tokens is None


def test_agent_yaml_max_tokens_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`agent` 节只从 YAML 读取（M5 已知坑）：显式写值必须被配置中心读进来。"""
    from app.config import get_agent_config

    config_file = tmp_path / "gateway.yaml"
    config_file.write_text(
        "agent:\n  enabled: true\n  mock: true\n  max_tokens: 256\n", encoding="utf-8"
    )
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(config_file))
    get_agent_config.cache_clear()
    try:
        assert get_agent_config().max_tokens == 256
    finally:
        get_agent_config.cache_clear()
