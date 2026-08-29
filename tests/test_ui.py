"""阶段 3：Agent Workbench UI 测试。

- GET /ui 返回静态工作台页面（含 <html）
- GET / 重定向到 /ui
- 无 Key 调 /agent/run 仍 401（鉴权不因 UI 存在而放松）
"""

from httpx import AsyncClient

AUTH_HEADERS = {"X-API-Key": "test-key"}


async def test_ui_index_served(client: AsyncClient) -> None:
    """GET /ui 返回 200 且为工作台 HTML 页面。"""
    resp = await client.get("/ui")
    assert resp.status_code == 200
    assert "<html" in resp.text
    assert "MCP Agent Workbench" in resp.text
    assert resp.headers["content-type"].startswith("text/html")


async def test_ui_static_assets_served(client: AsyncClient) -> None:
    """静态资源 app.js / style.css 可访问（工作台依赖）。"""
    js = await client.get("/ui/app.js")
    assert js.status_code == 200
    assert "runAgent" in js.text
    css = await client.get("/ui/style.css")
    assert css.status_code == 200
    assert "background" in css.text


async def test_ui_flow_panel_present(client: AsyncClient) -> None:
    """UI 改版：右侧「运行流程」可视化面板随页面下发（SVG 舞台 + 控制按钮）。"""
    resp = await client.get("/ui")
    assert resp.status_code == 200
    assert "flow-stage" in resp.text
    assert "运行流程" in resp.text
    assert "flow-dot" in resp.text


async def test_root_redirects_to_ui(client: AsyncClient) -> None:
    """GET / 302/307 重定向到 /ui。"""
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/ui"


async def test_agent_run_still_requires_key(gateway_client: AsyncClient) -> None:
    """无 Key 调 /agent/run 仍 401（UI 只是前端，鉴权语义不变）。"""
    resp = await gateway_client.post("/agent/run", json={"question": "有多少客户？"})
    assert resp.status_code == 401
    # 有 Key 正常 200
    ok = await gateway_client.post(
        "/agent/run", headers=AUTH_HEADERS, json={"question": "有多少客户？"}
    )
    assert ok.status_code == 200
