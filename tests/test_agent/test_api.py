"""Agent API 测试（阶段 2）：成功 / 401 / 429 / 503 全覆盖。

测试 Key 与 agent 配置见 tests/fixtures/gateway_test.yaml：
- test-key（60 次/分钟）、limited-key（2 次/分钟）、whitelist-key（只允许 demo_sql__ask）
- agent: {enabled: true, mock: true}（mock 不需要 LLM Key）
M1：新增白名单击穿验收用例（limited key 经 /agent/run 调白名单外工具被拒）。
"""

import json

from httpx import AsyncClient

AUTH_HEADERS = {"X-API-Key": "test-key"}
LIMITED_HEADERS = {"X-API-Key": "limited-key"}
WHITELIST_HEADERS = {"X-API-Key": "whitelist-key"}
HOURLY_HEADERS = {"X-API-Key": "hourly-key"}
# M6：能力位 Key —— 工具可见但禁止触发 Agent（见 tests/fixtures/gateway_test.yaml）
TOOLS_ONLY_HEADERS = {"X-API-Key": "tools-only-key"}


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


async def test_agent_run_hourly_rate_limited(gateway_client: AsyncClient) -> None:
    """M1 小时桶对 Agent 端点同样生效：hourly-key 每小时 2 次，第 3 次 429。"""
    assert (
        await gateway_client.post("/agent/run", headers=HOURLY_HEADERS, json={"question": "x"})
    ).status_code == 200
    assert (
        await gateway_client.post("/agent/run", headers=HOURLY_HEADERS, json={"question": "x"})
    ).status_code == 200
    resp = await gateway_client.post("/agent/run", headers=HOURLY_HEADERS, json={"question": "x"})
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 60


async def test_agent_run_disabled_returns_503(gateway_client: AsyncClient) -> None:
    """Agent 未启用（app.state.agent_runner 为 None）时返回 503 与明确提示。"""
    from app.main import app as gateway_app

    gateway_app.state.agent_runner = None
    resp = await gateway_client.post(
        "/agent/run", headers=AUTH_HEADERS, json={"question": "有多少客户？"}
    )
    assert resp.status_code == 503
    assert "agent.enabled" in resp.json()["detail"]


# ---------- M1 §5.3：白名单击穿验收（limited key 经 /agent/run 调白名单外工具被拒） ----------


class _ForceRunSqlModel:
    """模拟模型被诱导（幻觉/提示注入）：无视注入清单，直接要求调用 demo_sql__run_sql。"""

    @staticmethod
    async def chat(messages, tools):
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_forced",
                    "type": "function",
                    "function": {
                        "name": "demo_sql__run_sql",
                        "arguments": json.dumps({"sql": "SELECT * FROM customers"}),
                    },
                }
            ],
        }


async def test_agent_run_whitelist_bypass_blocked(gateway_client: AsyncClient, monkeypatch) -> None:
    """M1 验收主用例：whitelist-key 经 /agent/run 也调不了白名单外的 run_sql。

    即便模型强行生成白名单外工具调用，registry 收口的白名单校验也会拒绝——
    工具调用步骤记录 error，SQL 不会真正执行。
    """
    from app.main import app as gateway_app

    runner = gateway_app.state.agent_runner
    monkeypatch.setattr(runner, "_model", _ForceRunSqlModel())

    resp = await gateway_client.post(
        "/agent/run", headers=WHITELIST_HEADERS, json={"question": "用 SQL 查一下所有客户"}
    )
    assert resp.status_code == 200
    body = resp.json()

    # 注入侧收口：白名单 Key 只注入 1 个工具（不再向模型泄露全量清单）
    assert body["tools_total"] == 1
    assert body["tools_injected"] == 1

    # 执行侧收口：白名单外调用被拒并记录在步骤 Trace 中
    tool_call = next(s for s in body["steps"] if s["kind"] == "tool_call")
    assert tool_call["tool"] == "demo_sql__run_sql"
    assert tool_call["is_error"] is True
    assert "白名单" in tool_call["content"]
    tool_result = next(s for s in body["steps"] if s["kind"] == "tool_result")
    assert tool_result["is_error"] is True
    assert "customers" not in tool_result["content"]  # 查询没有真正执行


async def test_agent_run_whitelist_key_normal_flow(gateway_client: AsyncClient) -> None:
    """白名单 Key 的正常 Agent 链路不受影响：可见工具收窄，白名单内调用成功。"""
    resp = await gateway_client.post(
        "/agent/run", headers=WHITELIST_HEADERS, json={"question": "有多少客户？"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tools_total"] == 1
    assert body["tools_injected"] == 1
    assert "SELECT COUNT(*)" in body["answer"]


# ---------- M1 §5.3：入参设界（question / max_rounds 上限 → 422） ----------


async def test_agent_run_question_too_long_422(gateway_client: AsyncClient) -> None:
    """question 超过 2000 字符 → 422（接真实 LLM 后是成本放大器）。"""
    resp = await gateway_client.post(
        "/agent/run", headers=AUTH_HEADERS, json={"question": "问" * 2001}
    )
    assert resp.status_code == 422


async def test_agent_run_max_rounds_out_of_bounds_422(gateway_client: AsyncClient) -> None:
    """max_rounds 必须在 1~20：0 / 负数 / 超上限一律 422。"""
    for bad in (0, -1, 21, 100000):
        resp = await gateway_client.post(
            "/agent/run", headers=AUTH_HEADERS, json={"question": "x", "max_rounds": bad}
        )
        assert resp.status_code == 422, f"max_rounds={bad} 应被拒绝"


async def test_agent_run_max_rounds_upper_bound_accepted(gateway_client: AsyncClient) -> None:
    """max_rounds=1（合法上界内）正常放行。"""
    resp = await gateway_client.post(
        "/agent/run", headers=AUTH_HEADERS, json={"question": "有多少客户？", "max_rounds": 1}
    )
    assert resp.status_code == 200


# ---------- M6 / 0.5-A：Agent 能力位（公开 Key 即便泄露也触发不了模型） ----------


async def test_agent_run_forbidden_without_capability(gateway_client: AsyncClient) -> None:
    """agent_allowed=false 的 Key：两个 Agent 端点都 403（含流式），文案面向调用方。"""
    for path in ("/agent/run", "/agent/run/stream"):
        resp = await gateway_client.post(
            path, headers=TOOLS_ONLY_HEADERS, json={"question": "有多少客户？"}
        )
        assert resp.status_code == 403, path
        assert "无权调用 Agent" in resp.json()["detail"]


async def test_tools_only_key_can_still_call_mcp_tools(gateway_client: AsyncClient) -> None:
    """能力位只关"模型触发"，不关"MCP 工具调用"——同一个 Key 调工具仍然正常。"""
    resp = await gateway_client.post(
        "/tools/demo_sql__ask/call",
        headers=TOOLS_ONLY_HEADERS,
        json={"arguments": {"question": "有多少客户？"}},
    )
    assert resp.status_code == 200
    assert resp.json()["is_error"] is False


async def test_agent_capability_unset_stays_compatible(gateway_client: AsyncClient) -> None:
    """未声明 agent_allowed 的 Key（None）保持既有行为：允许触发 Agent（兼容旧配置）。"""
    resp = await gateway_client.post(
        "/agent/run", headers=AUTH_HEADERS, json={"question": "有多少客户？"}
    )
    assert resp.status_code == 200
