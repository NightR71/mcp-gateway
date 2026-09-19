"""M6 三态公开开关测试：管理接口（成功 / 401 / 403 / 429 / 409）+ 三态运行行为。

为什么三态行为也放在这里：它与管理接口共用同一套"切换手段"（依赖覆盖 `ModeStore`），
放一起才能一眼看出"接口切了态 → 请求行为随之变化"的完整因果。

管理员 Key 只存在于环境变量 `GATEWAY_ADMIN_KEY`（见 app/core/security.py），
所以本文件用 monkeypatch 注入**测试假值**，并额外断言它从不外泄到响应里。
管理员配额在 tests/fixtures/gateway_test.yaml 里故意压到 3 次/分钟（专测 429）。
"""

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from app.api.deps import get_mode_store
from app.config import get_resume_kb_config
from app.core.mode import ProcessModeStore
from app.main import app

TEST_ADMIN_KEY = "test-admin-key-fake-value-0123456789abcdef"  # 仅测试用假值，非任何真实凭据
ADMIN_HEADERS = {"X-API-Key": TEST_ADMIN_KEY}
NORMAL_HEADERS = {"X-API-Key": "test-key"}
VISITOR_HEADERS = {"X-API-Key": "visitor-key-please-change"}


@pytest.fixture
def admin_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """注入管理员 Key 环境变量（按请求读取，无需重启应用）。"""
    monkeypatch.setenv("GATEWAY_ADMIN_KEY", TEST_ADMIN_KEY)
    return TEST_ADMIN_KEY


@pytest.fixture
def writable_mode() -> AsyncIterator[ProcessModeStore]:
    """把开关替换成进程内可写实现，便于逐态验证可写部署的行为。"""
    store = ProcessModeStore("public")
    app.dependency_overrides[get_mode_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_mode_store, None)


@pytest.fixture
def fixed_mode() -> AsyncIterator[ProcessModeStore]:
    """同上，但用例只改状态、不测接口写入（可读可写，初始 public）。"""
    store = ProcessModeStore("public")
    app.dependency_overrides[get_mode_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_mode_store, None)


# ---------------------------------------------------------------------------
# 管理接口：鉴权与限流（项目约定的三 case）
# ---------------------------------------------------------------------------


async def test_admin_mode_requires_key(gateway_client: AsyncClient) -> None:
    """无 Key → 401；普通 Key → 403（不透露"该怎么申请"）。

    本用例**故意不设** `GATEWAY_ADMIN_KEY`：此时即使请求头带着"看起来像管理员 Key"的值，
    也必须 401——管理员身份来自环境变量，不存在任何默认口令或兜底放行。
    """
    assert (await gateway_client.get("/admin/mode")).status_code == 401
    resp = await gateway_client.get("/admin/mode", headers=NORMAL_HEADERS)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "需要管理员权限"
    assert (await gateway_client.get("/admin/mode", headers=ADMIN_HEADERS)).status_code == 401


async def test_admin_mode_rate_limited(gateway_client: AsyncClient, admin_key: str) -> None:
    """管理员配额 3 次/分钟（夹具）：第 4 次 429 且带 Retry-After。"""
    for _ in range(3):
        assert (await gateway_client.get("/admin/mode", headers=ADMIN_HEADERS)).status_code == 200
    resp = await gateway_client.get("/admin/mode", headers=ADMIN_HEADERS)
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After")


async def test_admin_mode_get_reports_state_without_leaking_key(
    gateway_client: AsyncClient, admin_key: str
) -> None:
    """成功 case：如实上报态 / 来源 / 可写性，且响应里不含 Key 明文。"""
    resp = await gateway_client.get("/admin/mode", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "public"  # 夹具 gateway.public_mode
    assert body["source"] in {"env", "yaml", "default", "process"}
    assert body["writable"] is False  # 夹具用默认的 config 存储 = 只读
    assert body["note"]
    assert TEST_ADMIN_KEY not in resp.text, "管理接口响应泄露了管理员 Key"


async def test_admin_mode_write_rejected_on_readonly_deployment(
    gateway_client: AsyncClient, admin_key: str
) -> None:
    """只读部署（多实例 Serverless）：写入 409 + 确定性切换指引，而不是假装成功。"""
    resp = await gateway_client.post("/admin/mode", headers=ADMIN_HEADERS, json={"mode": "closed"})
    assert resp.status_code == 409
    assert "新部署" in resp.json()["detail"]
    assert TEST_ADMIN_KEY not in resp.text


async def test_admin_mode_write_succeeds_on_single_process_deployment(
    gateway_client: AsyncClient, admin_key: str, writable_mode: ProcessModeStore
) -> None:
    """单进程部署：写入立即生效并回读一致（docker / 本机 / 单实例 VPS 的"一键切换"）。"""
    resp = await gateway_client.post("/admin/mode", headers=ADMIN_HEADERS, json={"mode": "closed"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "closed"
    assert body["writable"] is True
    assert body["source"] == "process"
    assert writable_mode.get() == "closed"
    # 回读一致
    again = await gateway_client.get("/admin/mode", headers=ADMIN_HEADERS)
    assert again.json()["mode"] == "closed"


async def test_admin_mode_rejects_unknown_state(
    gateway_client: AsyncClient, admin_key: str
) -> None:
    """非法态值 → 422（Pydantic Literal 拦截，不会写进开关）。"""
    resp = await gateway_client.post("/admin/mode", headers=ADMIN_HEADERS, json={"mode": "opened"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 三态运行行为：公开 / 内部 / 关闭
# ---------------------------------------------------------------------------


async def test_public_mode_allows_visitor(
    gateway_client: AsyncClient, admin_key: str, fixed_mode: ProcessModeStore
) -> None:
    """public：普通 Key 正常问答，状态里如实上报 public。"""
    resp = await gateway_client.post(
        "/agent/run", headers=NORMAL_HEADERS, json={"question": "有多少客户？"}
    )
    assert resp.status_code == 200
    status = await gateway_client.get("/agent/status")
    assert status.json()["public_mode"] == "public"


async def test_switch_controls_usable_flag(
    gateway_client: AsyncClient,
    admin_key: str,
    fixed_mode: ProcessModeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`usable` 是前端"能否问答"的合一判据：开关不是 public 时一律 false。

    夹具配置里 allow_mock_demo=false（mock 剧本不对外冒充回答，决策 6），
    这里临时把它打开，才能把"三态开关"这一项单独隔离出来验证。
    """
    from app.api.routes import agent as agent_route

    config = get_resume_kb_config().model_copy(deep=True)
    config.frontend.allow_mock_demo = True
    monkeypatch.setattr(agent_route, "get_resume_kb_config", lambda: config)

    assert (await gateway_client.get("/agent/status")).json()["usable"] is True
    for state in ("internal", "closed"):
        fixed_mode.set(state)
        body = (await gateway_client.get("/agent/status")).json()
        assert body["usable"] is False, state
        assert body["public_mode"] == state


async def test_internal_mode_blocks_visitor_but_allows_admin(
    gateway_client: AsyncClient, admin_key: str, fixed_mode: ProcessModeStore
) -> None:
    """internal：对外维护（503 + 维护文案），管理员仍可自测。"""
    fixed_mode.set("internal")

    visitor = await gateway_client.post(
        "/agent/run", headers=NORMAL_HEADERS, json={"question": "有多少客户？"}
    )
    assert visitor.status_code == 503
    assert visitor.json()["detail"]  # 维护文案（配置化，非错误码）

    admin = await gateway_client.post(
        "/agent/run", headers=ADMIN_HEADERS, json={"question": "有多少客户？"}
    )
    assert admin.status_code == 200

    status = await gateway_client.get("/agent/status")
    assert status.json()["public_mode"] == "internal"
    assert status.json()["usable"] is False


async def test_closed_mode_blocks_everyone(
    gateway_client: AsyncClient, admin_key: str, fixed_mode: ProcessModeStore
) -> None:
    """closed：一律维护（含管理员）——它是成本应急闸门，必须能一刀关死。"""
    fixed_mode.set("closed")
    for headers in (NORMAL_HEADERS, ADMIN_HEADERS):
        resp = await gateway_client.post(
            "/agent/run", headers=headers, json={"question": "有多少客户？"}
        )
        assert resp.status_code == 503
    status = await gateway_client.get("/agent/status")
    assert status.json()["usable"] is False
    assert status.json()["public_mode"] == "closed"


async def test_switch_applies_to_streaming_endpoint_too(
    gateway_client: AsyncClient, admin_key: str, fixed_mode: ProcessModeStore
) -> None:
    """流式端点与同步端点共用同一套收口（切换后同样 503）。"""
    fixed_mode.set("closed")
    resp = await gateway_client.post(
        "/agent/run/stream", headers=NORMAL_HEADERS, json={"question": "有多少客户？"}
    )
    assert resp.status_code == 503


async def test_switch_blocks_public_visitor_key_on_kb_client(
    resume_kb_client: AsyncClient, admin_key: str, fixed_mode: ProcessModeStore
) -> None:
    """线上口径复现：internal 态下**公开的访客 Key** 被维护页挡住（面试官视角）。"""
    fixed_mode.set("internal")
    resp = await resume_kb_client.post(
        "/agent/run/stream", headers=VISITOR_HEADERS, json={"question": "介绍一下你自己"}
    )
    assert resp.status_code == 503
    # 三态只关模型路径：MCP 工具调用不受影响（A+D 负责的"网关入口"是另一道闸门）
    tools = await resume_kb_client.get("/tools", headers=VISITOR_HEADERS)
    assert tools.status_code == 200
