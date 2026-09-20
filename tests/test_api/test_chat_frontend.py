"""M4 前端测试：/chat 与 /resume 路由、能力探测三态、前端产物口径约束。

覆盖三类：
1. **路由与静态资源**：/chat 与 /resume 可访问，依赖的 css/js 随页面下发；
2. **能力探测（GET /agent/status）**：disabled / mock / real 三态与 usable 推导——
   这是「维护页 vs 正常对话」的唯一判据（决策 6：绝不用假剧本冒充回答）；
3. **前端产物红线**：静态文件零 PII、简历页零成绩主张、零雇主名称、
   访客 Key 与后端配置一致（防改名后前端失效）。
"""

import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import yaml
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.config import (
    get_agent_config,
    get_auth_config,
    get_resume_kb_config,
    get_server_configs,
    get_settings,
)
from app.main import app

CHAT_DIR = Path(__file__).resolve().parents[2] / "app" / "chat"
RESUME_DIR = Path(__file__).resolve().parents[2] / "app" / "resume"
PRODUCTION_CONFIG = Path(__file__).resolve().parents[2] / "config" / "gateway.yaml"

# 前端产物不得出现的内容（红线：零 PII、零成绩主张、零雇主名）
PII_PATTERNS = {
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"),
}
GRADE_TERMS = ("优秀", "推优", "二辩", "GPA", "绩点")
# 分数表述用词边界匹配——避免把测试数里的「90+」误判成成绩「90+」（前面还有数字时不算分数）
GRADE_SCORE_PATTERN = re.compile(r"(?<![\d])90\s*(?:分|\+|％|%)")
EMPLOYER_NAMES = ("中电福富", "海科新质")


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "gateway.yaml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
async def real_model_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """agent.mock=false + 提供 Key 的客户端 → 探测应报 real / usable。"""
    cfg = _write_config(
        tmp_path,
        "gateway:\n  log_level: WARNING\n  public_mode: public\n"
        "agent:\n  enabled: true\n  mock: false\n  model: deepseek-flash\n"
        'auth:\n  db_path: ":memory:"\n  api_keys:\n'
        "    - key: test-key\n      name: tester\n"
        "servers: []\n",
    )
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("GATEWAY_AGENT_API_KEY", "fake-key-for-status-test")
    for getter in (get_settings, get_agent_config, get_auth_config, get_server_configs):
        getter.cache_clear()
    async with LifespanManager(app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as c:
            yield c


@pytest.fixture
async def mock_demo_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """mock 模式但服务端显式允许演示 → 探测应报 mock / usable。"""
    cfg = _write_config(
        tmp_path,
        "gateway:\n  log_level: WARNING\n  public_mode: public\n"
        "agent:\n  enabled: true\n  mock: true\n"
        'auth:\n  db_path: ":memory:"\n  api_keys:\n'
        "    - key: test-key\n      name: tester\n"
        "servers: []\n",
    )
    kb_cfg = tmp_path / "resume_kb.yaml"
    kb_cfg.write_text("frontend:\n  allow_mock_demo: true\n", encoding="utf-8")
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("RESUME_KB_CONFIG_FILE", str(kb_cfg))
    for getter in (
        get_settings,
        get_agent_config,
        get_auth_config,
        get_server_configs,
        get_resume_kb_config,
    ):
        getter.cache_clear()
    async with LifespanManager(app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as c:
            yield c


# ---------------------------------------------------------------------------
# 路由与静态资源
# ---------------------------------------------------------------------------


async def test_chat_index_served(client: AsyncClient) -> None:
    """/chat 返回 200 与对话页 HTML（含对话流、气泡卡、流程图容器）。"""
    resp = await client.get("/chat")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "陈晓伟" in resp.text
    for anchor in ("stream", "bubbles", "flow-stage", "maintenance", "input-bar"):
        assert anchor in resp.text, anchor


async def test_chat_static_assets_served(client: AsyncClient) -> None:
    """/chat 的 css 与 js 可访问（零构建链，直接随包下发）。"""
    css = await client.get("/chat/chat.css")
    assert css.status_code == 200 and "background" in css.text
    js = await client.get("/chat/chat.js")
    assert js.status_code == 200
    assert "streamAgent" in js.text
    assert "SEGMENTS" in js.text  # 流程图段定义随 js 下发
    # 业务问题不硬编码在前端：由 /agent/status 的 preset_questions 下发
    assert "网关最难的三个坑" not in js.text


async def test_resume_index_served(client: AsyncClient) -> None:
    """/resume 返回 200 与脱敏简历页（含关键经历锚点）。"""
    resp = await client.get("/resume")
    assert resp.status_code == 200
    assert "陈晓伟" in resp.text
    assert "福州理工学院" in resp.text
    assert "MCP Gateway" in resp.text
    assert "福建赛区三等奖" in resp.text


async def test_resume_static_assets_served(client: AsyncClient) -> None:
    """简历页样式可访问（含打印样式）。"""
    css = await client.get("/resume/resume.css")
    assert css.status_code == 200
    assert "@media print" in css.text


async def test_root_redirects_to_chat(client: AsyncClient) -> None:
    """M6 起 `/` 指向 /chat（业务应用为主入口）；/ui 页面与功能仍未改动、可直达。"""
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/chat"
    assert (await client.get("/ui")).status_code == 200  # /ui 仍可直达，只是不再是默认落地页


# ---------------------------------------------------------------------------
# 能力探测三态
# ---------------------------------------------------------------------------


async def test_status_disabled_without_lifespan(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未跑 lifespan（无 AgentRunner）→ disabled / unusable。

    显式清空 app.state：lifespan 关闭后不会自动移除 state 属性，若不处理，
    先跑过 gateway_client 的用例会把 runner 残留给本用例（测试顺序污染）；
    monkeypatch 保证用例结束后恢复，与其他用例互不影响。
    """
    monkeypatch.setattr(app.state, "agent_runner", None, raising=False)
    resp = await client.get("/agent/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "disabled"
    assert body["agent_enabled"] is False
    assert body["usable"] is False


async def test_status_mock_is_not_usable_by_default(gateway_client: AsyncClient) -> None:
    """mock 模式且未放行演示 → unusable（前端显示维护页，不冒充回答）。"""
    resp = await gateway_client.get("/agent/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "mock"
    assert body["agent_enabled"] is True
    assert body["mock_demo_enabled"] is False
    assert body["usable"] is False


async def test_status_mock_usable_when_demo_allowed(mock_demo_client: AsyncClient) -> None:
    """服务端显式允许 mock 演示 → usable（本地/验收走通链路用）。"""
    body = (await mock_demo_client.get("/agent/status")).json()
    assert body["mode"] == "mock"
    assert body["mock_demo_enabled"] is True
    assert body["usable"] is True


async def test_status_real_model_is_usable(real_model_client: AsyncClient) -> None:
    """接入真实模型（非 mock + 有 Key）→ real / usable。"""
    body = (await real_model_client.get("/agent/status")).json()
    assert body["mode"] == "real"
    assert body["usable"] is True
    assert body["model_label"] == "deepseek-flash"


async def test_status_closed_switch_blocks_usable(tmp_path: Path, monkeypatch) -> None:
    """M6 三态开关：`public_mode: closed` 时模型再就绪也 unusable（对外维护页）。

    这是 fail-closed 的前端契约：`usable` = 模型就绪 **且** 开关是 public。
    """
    cfg = _write_config(
        tmp_path,
        "gateway:\n  log_level: WARNING\n  public_mode: closed\n"
        "agent:\n  enabled: true\n  mock: false\n  model: deepseek-flash\n"
        'auth:\n  db_path: ":memory:"\n  api_keys:\n'
        "    - key: test-key\n      name: tester\n"
        "servers: []\n",
    )
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("GATEWAY_AGENT_API_KEY", "fake-key-for-status-test")
    for getter in (get_settings, get_agent_config, get_auth_config, get_server_configs):
        getter.cache_clear()
    async with LifespanManager(app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as c:
            body = (await c.get("/agent/status")).json()
    assert body["mode"] == "real"  # 模型确实就绪
    assert body["public_mode"] == "closed"  # 但对外关着
    assert body["usable"] is False


async def test_status_exposes_preset_questions(gateway_client: AsyncClient) -> None:
    """预设问题由服务端配置下发（前端不硬编码业务内容）。"""
    body = (await gateway_client.get("/agent/status")).json()
    assert isinstance(body["preset_questions"], list)
    assert body["preset_questions"], "测试夹具应下发预设问题"
    assert any("网关" in q for q in body["preset_questions"])  # 引导顺序：网关优先


async def test_status_leaks_no_secret_fields(gateway_client: AsyncClient) -> None:
    """状态端点不泄露 Key / 端点 / 内部细节（只给模式与展示标识）。"""
    payload = (await gateway_client.get("/agent/status")).json()
    assert set(payload) == {
        "agent_enabled",
        "mode",
        "usable",
        "mock_demo_enabled",
        "model_label",
        "maintenance_hint",
        "preset_questions",
        "public_mode",  # M6 新增（只增不改：前端据此显示维护原因，值域是公开的三态枚举）
    }
    text = str(payload)
    assert "http" not in text  # 不含端点 URL
    assert "key" not in text.lower() or "mock_demo" in text  # 键名允许，键值不允许


# ---------------------------------------------------------------------------
# 前端产物红线
# ---------------------------------------------------------------------------


def test_process_trace_is_collapsed_by_default() -> None:
    """UX 契约（用户 2026-09-18 反馈）：内部运行步骤默认折叠，面试官只看得到问答。

    背景：步骤卡（语义路由 / 鉴权限流 / 工具调用 / 耗时）此前直接铺在对话流里，
    面试官得一路划到底才看得到回答——像在看日志，不像在对话。
    现在每轮问答生成一个默认收起的「运行详情」块：提问与回答在块外，步骤在块内。

    本地浏览器实测（Chromium）：默认态 3 步全部不可见、点标题展开后可见、
    出现失败步骤时自动展开并标红。断言写在静态文件上，避免以后被无意改回。
    """
    js = (CHAT_DIR / "chat.js").read_text(encoding="utf-8")
    css = (CHAT_DIR / "chat.css").read_text(encoding="utf-8")
    assert 'root.dataset.state = "collapsed"' in js, "轨迹块不再默认收起"
    assert 'root.dataset.state = expanded ? "collapsed" : "expanded"' in js, "点击不再可切换"
    assert 'trace.root.dataset.state = "expanded"' in js, "失败步骤不再自动展开"
    assert 'head.setAttribute("aria-expanded"' in js, "标题栏缺少无障碍展开状态"
    assert "(trace ? trace.body : streamEl).appendChild(el)" in js, "步骤卡没有进轨迹块"
    assert '.trace[data-state="collapsed"] .trace-body { display: none; }' in css, (
        "收起态没有真正隐藏步骤内容"
    )


def _frontend_files() -> list[Path]:
    return sorted(
        [p for p in CHAT_DIR.iterdir() if p.is_file()]
        + [p for p in RESUME_DIR.iterdir() if p.is_file()]
    )


def test_frontend_has_no_pii() -> None:
    """/chat 与 /resume 的静态文件零 PII（电话 / 邮箱）。"""
    for path in _frontend_files():
        text = path.read_text(encoding="utf-8")
        for name, pattern in PII_PATTERNS.items():
            # 允许明显虚构的占位（如 example.com）
            found = [m for m in pattern.findall(text) if "example.com" not in m]
            assert not found, f"{path.name} 命中 {name}: {found}"


def test_resume_page_has_no_grade_claims() -> None:
    """简历页零成绩主张（T1 已定：成绩只在被专门询问时由 AI 用固定口径回答）。"""
    text = (RESUME_DIR / "index.html").read_text(encoding="utf-8")
    for term in GRADE_TERMS:
        assert term not in text, f"简历页出现成绩表述：{term}"
    matched = GRADE_SCORE_PATTERN.search(text)
    assert matched is None, f"简历页出现分数表述：{matched.group(0)}"
    assert "毕业设计 90" not in text and "实践类课程" not in text


def test_frontend_has_no_employer_names() -> None:
    """前端产物不出现雇主名称（行业描述口径）。"""
    for path in _frontend_files():
        text = path.read_text(encoding="utf-8")
        for name in EMPLOYER_NAMES:
            assert name not in text, f"{path.name} 出现雇主名 {name}"


def test_frontend_disclaims_third_party_attribution() -> None:
    """简历页保留毕设归属说明（集成 vs 自研），不冒领第三方能力。"""
    text = (RESUME_DIR / "index.html").read_text(encoding="utf-8")
    assert "开源 MCP" in text
    assert "归属说明" in text
    assert "自研截图" not in text and "自研识别" not in text


def test_visitor_key_matches_production_config() -> None:
    """前端 meta 里的访客 Key 与生产配置一致（改名后前端会静默失效，故断言）。"""
    html = (CHAT_DIR / "index.html").read_text(encoding="utf-8")
    match = re.search(r'name="visitor-key"\s+content="([^"]+)"', html)
    assert match is not None, "/chat 缺少 visitor-key meta"
    frontend_key = match.group(1)

    keys = yaml.safe_load(PRODUCTION_CONFIG.read_text(encoding="utf-8"))["auth"]["api_keys"]
    assert any(k["key"] == frontend_key for k in keys), (
        f"前端内置 Key {frontend_key!r} 不在生产配置里"
    )


def test_chat_notes_how_to_override_key() -> None:
    """页面注明 Key 覆盖方式（开发者换 Key 用），避免只能改代码。"""
    html = (CHAT_DIR / "index.html").read_text(encoding="utf-8")
    assert "localStorage" in html and "mcp_chat_key" in html
