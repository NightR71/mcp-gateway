"""统一工具 API 测试：成功 / 401 未授权 / 429 限流 / 404 未知工具 全覆盖。

测试 Key 定义见 tests/fixtures/gateway_test.yaml：
- test-key（60 次/分钟）、limited-key（2 次/分钟）
"""

from httpx import AsyncClient

AUTH_HEADERS = {"X-API-Key": "test-key"}
LIMITED_HEADERS = {"X-API-Key": "limited-key"}


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
