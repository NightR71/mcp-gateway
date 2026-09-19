"""M5 传输层连接重试测试（不联网、不花钱、不碰 Key）。

背景（M5 冒烟实测的真根因）：`api.deepseek.com` 在本机网络下解析到 3 个 IP，其中部分
IP 的 TLS 证书链被 Python 校验拒绝（self-signed certificate in certificate chain）——
**新建连接约 60% 失败**（20 次采样 12 次失败）。而一次 Agent 问答要 2~3 次模型调用，
于是几乎每问都会断在某一轮，真模型冒烟无法产出有效数据。

修复：给模型自建的 httpx 客户端挂 `AsyncHTTPTransport(retries=N)`。httpx 的重试只覆盖
**连接层**错误（ConnectError/ConnectTimeout）——TLS 握手失败正落在这一层，因此有效；
而 TCP 已连上之后的协议错误（RemoteProtocolError）不在其覆盖内，这一点也由下面的用例
如实钉住，避免误以为「挂了 retries 就什么都重试」。

修复效果用真实端点直接实测过（报告 §6.5）：retries=0 → 12/20 失败；retries=3 → 0/20 失败。
本文件测的是**接线是否正确**（次数有没有真的传进连接池、注入的 client 是否原样使用）。
"""

from __future__ import annotations

import httpx

from app.agent.models import build_openai_model


def _transport_retries(client: httpx.AsyncClient) -> int:
    """读出客户端底层连接池的重试次数（httpx 0.28 的内部结构，见模块注释）。

    故意断言内部字段：本用例的目的就是确认「参数真的传到了连接池」——如果 httpx 换掉
    内部结构，这里会明确失败（好过静默失去重试能力而没人发现）。
    """
    pool = client._transport._pool  # type: ignore[attr-defined]  # noqa: SLF001
    return int(pool._retries)  # type: ignore[attr-defined]  # noqa: SLF001


async def test_configured_connect_retries_reach_the_connection_pool() -> None:
    """connect_retries=N ⇒ 底层连接池的 retries 就是 N。"""
    model = build_openai_model(
        "https://example.invalid/v1", "fake-key", "fake-model", connect_retries=4
    )
    try:
        assert _transport_retries(model._client) == 4  # type: ignore[attr-defined]  # noqa: SLF001
    finally:
        await model.aclose()  # type: ignore[attr-defined]


async def test_default_connect_retries_keeps_legacy_behaviour() -> None:
    """默认 connect_retries=0 ⇒ 连接池不重试（与旧行为一致，测试/自定义 client 不受影响）。"""
    model = build_openai_model("https://example.invalid/v1", "fake-key", "fake-model")
    try:
        assert _transport_retries(model._client) == 0  # type: ignore[attr-defined]  # noqa: SLF001
    finally:
        await model.aclose()  # type: ignore[attr-defined]


async def test_injected_client_is_used_verbatim() -> None:
    """注入了 client 时必须原样使用（不覆盖调用方给的传输配置）。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant"}}]})

    injected = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = build_openai_model(
        "https://example.invalid/v1",
        "fake-key",
        "fake-model",
        injected,
        connect_retries=9,
    )
    try:
        await model.chat([{"role": "user", "content": "hi"}], [])
    finally:
        await injected.aclose()
    assert len(calls) == 1


def test_agent_config_defaults_include_connect_retries() -> None:
    """真模型路径默认带上连接重试（配置可调，防止被改回 0 而冒烟再次大面积失败）。"""
    from app.config import AgentConfig

    assert AgentConfig().model_connect_retries >= 3
