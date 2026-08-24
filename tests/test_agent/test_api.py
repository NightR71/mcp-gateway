"""Agent API 测试（阶段 2）：成功 / 401 / 429 / 503 全覆盖。

测试 Key 与 agent 配置见 tests/fixtures/gateway_test.yaml：
- test-key（60 次/分钟）、limited-key（2 次/分钟）
- agent: {enabled: true, mock: true}（mock 不需要 LLM Key）
"""

from httpx import AsyncClient

AUTH_HEADERS = {"X-API-Key": "test-key"}
LIMITED_HEADERS = {"X-API-Key": "limited-key"}


async def test_agent_run_ok(gateway_client: AsyncClient) -> None:
    """agent.mock=true 时 /agent/run 返回 200 + answer + 非空 steps。"""
    resp = await gateway_client.post(
        "/agent/run", headers=AUTH_HEADERS, json={"question": "有多少客户？"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"]
    assert "SELECT COUNT(*)" in body["answer"]  # mock 调用了 demo_sql__ask（NL2SQL 全链路）
    assert body["steps"], "steps 必须非空"
    assert body["tools_total"] == 4
    assert body["tools_injected"] <= body["tools_total"]
    assert body["rounds"] >= 1
    # 时间线种类完整：user → tool_select → tool_call → tool_result → final
    kinds = [s["kind"] for s in body["steps"]]
    assert kinds[0] == "user"
    assert "tool_select" in kinds and "tool_call" in kinds and "tool_result" in kinds
    assert kinds[-1] == "final"
    tool_call = next(s for s in body["steps"] if s["kind"] == "tool_call")
    assert tool_call["tool"] == "demo_sql__ask"


async def test_agent_run_unauthorized(gateway_client: AsyncClient) -> None:
    """无 Key / 错误 Key 一律 401（鉴权先于一切）。"""
    assert (
        await gateway_client.post("/agent/run", json={"question": "有多少客户？"})
    ).status_code == 401
    resp = await gateway_client.post(
        "/agent/run", headers={"X-API-Key": "wrong-key"}, json={"question": "x"}
    )
    assert resp.status_code == 401


async def test_agent_run_rate_limited(gateway_client: AsyncClient) -> None:
    """limited-key 配额 2 次/分钟：第三次 429 且带 Retry-After。"""
    assert (
        await gateway_client.post("/agent/run", headers=LIMITED_HEADERS, json={"question": "x"})
    ).status_code == 200
    assert (
        await gateway_client.post("/agent/run", headers=LIMITED_HEADERS, json={"question": "x"})
    ).status_code == 200
    resp = await gateway_client.post("/agent/run", headers=LIMITED_HEADERS, json={"question": "x"})
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) >= 1


async def test_agent_run_disabled_returns_503(gateway_client: AsyncClient) -> None:
    """Agent 未启用（app.state.agent_runner 为 None）时返回 503 与明确提示。"""
    from app.main import app as gateway_app

    gateway_app.state.agent_runner = None
    resp = await gateway_client.post(
        "/agent/run", headers=AUTH_HEADERS, json={"question": "有多少客户？"}
    )
    assert resp.status_code == 503
    assert "agent.enabled" in resp.json()["detail"]
