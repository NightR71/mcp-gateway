"""统一工具 API 测试：成功 / 401 未授权 / 429 限流 / 404 未知工具 全覆盖。

测试 Key 定义见 tests/fixtures/gateway_test.yaml：
- test-key（60 次/分钟）、limited-key（2 次/分钟）
"""

from httpx import AsyncClient

AUTH_HEADERS = {"X-API-Key": "test-key"}
LIMITED_HEADERS = {"X-API-Key": "limited-key"}
WHITELIST_HEADERS = {"X-API-Key": "whitelist-key"}


async def test_list_tools_ok(gateway_client: AsyncClient) -> None:
    resp = await gateway_client.get("/tools", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    tools = resp.json()
    # 阶段 4：demo_sql server 升级为 4 个工具（echo / ask / run_sql / list_tables）
    assert {t["name"] for t in tools} == {
        "demo_sql__echo",
        "demo_sql__ask",
        "demo_sql__run_sql",
        "demo_sql__list_tables",
    }
    assert all(t["server"] == "demo_sql" for t in tools)
    echo = next(t for t in tools if t["name"] == "demo_sql__echo")
    assert "message" in echo["input_schema"]["properties"]


async def test_list_tools_unauthorized(gateway_client: AsyncClient) -> None:
    # 缺 X-API-Key 头
    assert (await gateway_client.get("/tools")).status_code == 401
    # Key 无效
    resp = await gateway_client.get("/tools", headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 401


async def test_call_tool_ok(gateway_client: AsyncClient) -> None:
    resp = await gateway_client.post(
        "/tools/demo_sql__echo/call",
        headers=AUTH_HEADERS,
        json={"arguments": {"message": "hello"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_error"] is False
    assert body["content"][0]["text"] == "echo: hello"


async def test_call_tool_ask_nl2sql(gateway_client: AsyncClient) -> None:
    """NL2SQL 全链路：自然语言 → SQL → 执行，经网关返回答案。"""
    resp = await gateway_client.post(
        "/tools/demo_sql__ask/call",
        headers=AUTH_HEADERS,
        json={"arguments": {"question": "有多少客户？"}},
    )
    assert resp.status_code == 200
    text = resp.json()["content"][0]["text"]
    assert "SELECT COUNT(*)" in text
    assert "5" in text  # 种子数据共 5 个客户


async def test_call_tool_not_found(gateway_client: AsyncClient) -> None:
    resp = await gateway_client.post("/tools/no__such_tool/call", headers=AUTH_HEADERS, json={})
    assert resp.status_code == 404


async def test_call_tool_unauthorized(gateway_client: AsyncClient) -> None:
    resp = await gateway_client.post(
        "/tools/demo_sql__echo/call", json={"arguments": {"message": "hi"}}
    )
    assert resp.status_code == 401


async def test_rate_limit_exceeded(gateway_client: AsyncClient) -> None:
    """limited-key 配额 2 次/分钟：前两次放行，第三次 429 且带 Retry-After。"""
    assert (await gateway_client.get("/tools", headers=LIMITED_HEADERS)).status_code == 200
    assert (await gateway_client.get("/tools", headers=LIMITED_HEADERS)).status_code == 200

    resp = await gateway_client.get("/tools", headers=LIMITED_HEADERS)
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) >= 1

    # 正常额度的 test-key 不受 limited-key 限流影响
    assert (await gateway_client.get("/tools", headers=AUTH_HEADERS)).status_code == 200


# ---------- 阶段 1：语义工具路由（GET /tools?query=&top_k=） ----------


async def test_list_tools_query_returns_sales_tool_first(gateway_client: AsyncClient) -> None:
    """`GET /tools?query=销售额&top_k=2`：过滤生效、demo_sql__ask 排前、top_k 截断。

    tests/fixtures/gateway_test.yaml 的 routing.min_tools=2（< 4 个工具），过滤生效。
    """
    resp = await gateway_client.get(
        "/tools", headers=AUTH_HEADERS, params={"query": "销售额", "top_k": 2}
    )
    assert resp.status_code == 200
    tools = resp.json()
    names = [t["name"] for t in tools]
    assert len(tools) <= 2
    assert len(tools) < 4  # 确实发生了过滤（不是全量返回）
    assert names[0] == "demo_sql__ask"
    assert "demo_sql__ask" in names


async def test_list_tools_no_query_returns_all(gateway_client: AsyncClient) -> None:
    """不带 query 时行为与一阶段一致：全量返回 4 个工具。"""
    resp = await gateway_client.get("/tools", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 4


async def test_list_tools_query_disabled_returns_all(
    gateway_client: AsyncClient, monkeypatch
) -> None:
    """routing.enabled=false 时 query 参数不生效，仍全量返回（兼容旧行为）。"""
    import app.api.routes.tools as tools_route

    monkeypatch.setattr(tools_route, "router_enabled", lambda: False)
    resp = await gateway_client.get("/tools", headers=AUTH_HEADERS, params={"query": "销售额"})
    assert resp.status_code == 200
    assert len(resp.json()) == 4


# ---------- 阶段 4：SSE 流式工具调用（/tools/{name}/call/stream） ----------


async def test_call_tool_stream_ok(gateway_client: AsyncClient) -> None:
    """流式工具调用：start → result → done；同步端点行为不变。"""
    async with gateway_client.stream(
        "POST",
        "/tools/demo_sql__echo/call/stream",
        headers=AUTH_HEADERS,
        json={"arguments": {"message": "hello"}},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk

    frames = [f for f in body.split("\n\n") if f.strip()]
    names = [f.split("\n")[0].replace("event: ", "") for f in frames]
    assert names == ["start", "result", "done"]
    assert "echo: hello" in body


async def test_call_tool_stream_unauthorized(gateway_client: AsyncClient) -> None:
    """无 Key 调 call/stream 仍 401（鉴权语义与同步端点一致）。"""
    resp = await gateway_client.post("/tools/demo_sql__echo/call/stream", json={})
    assert resp.status_code == 401


# ---------- 阶段 6：工具白名单（按 Key 可见性 + 403） ----------


async def test_whitelist_key_lists_only_allowed_tools(gateway_client: AsyncClient) -> None:
    """白名单 Key 的 /tools 只返回被授权的工具（demo_sql__ask）。"""
    resp = await gateway_client.get("/tools", headers=WHITELIST_HEADERS)
    assert resp.status_code == 200
    tools = resp.json()
    assert [t["name"] for t in tools] == ["demo_sql__ask"]


async def test_whitelist_key_call_allowed_tool_ok(gateway_client: AsyncClient) -> None:
    """白名单内的工具正常调用（200）。"""
    resp = await gateway_client.post(
        "/tools/demo_sql__ask/call",
        headers=WHITELIST_HEADERS,
        json={"arguments": {"question": "有多少客户？"}},
    )
    assert resp.status_code == 200
    assert "SELECT COUNT(*)" in resp.json()["content"][0]["text"]


async def test_whitelist_key_call_forbidden_tool_403(gateway_client: AsyncClient) -> None:
    """白名单外真实工具返回 403（区别于真不存在的 404）。"""
    resp = await gateway_client.post(
        "/tools/demo_sql__run_sql/call", headers=WHITELIST_HEADERS, json={}
    )
    assert resp.status_code == 403


async def test_whitelist_key_stream_forbidden_tool_403(gateway_client: AsyncClient) -> None:
    """流式端点同样受白名单约束（防止经 call/stream 绕过白名单）。"""
    resp = await gateway_client.post(
        "/tools/demo_sql__run_sql/call/stream", headers=WHITELIST_HEADERS, json={}
    )
    assert resp.status_code == 403


async def test_whitelist_key_unknown_tool_still_404(gateway_client: AsyncClient) -> None:
    """白名单 Key 调不存在工具仍是 404（403 只针对真实存在但不可见的工具）。"""
    resp = await gateway_client.post(
        "/tools/no__such_tool/call", headers=WHITELIST_HEADERS, json={}
    )
    assert resp.status_code == 404


async def test_full_key_behavior_unchanged(gateway_client: AsyncClient) -> None:
    """未配置 allowed_tools 的全量 Key：/tools 仍 4 个、任意工具可调。"""
    resp = await gateway_client.get("/tools", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 4

    resp = await gateway_client.post(
        "/tools/demo_sql__run_sql/call",
        headers=AUTH_HEADERS,
        json={"arguments": {"sql": "SELECT 1"}},
    )
    assert resp.status_code == 200
