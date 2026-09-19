"""M6 拨测契约测试：`.github/workflows/demo-health.yml` 的硬约束。

为什么值得钉住：拨测是"线上坏了但没人知道"的唯一兜底，而 workflow 文件很容易在后续
改动中悄悄退化（定时被删、断言被放宽、默认 Key 换成一个能烧模型余额的、失败不再告警）。
这些退化不会让任何测试变红——除非这里钉住它们。

四条红线：
1. **必须真定时**（`schedule`），不能只有手动触发——否则"拨测"名存实亡；
2. **默认只用公开的访客 Key**，且不得出现已退役的公开演示 Key、不得出现模型 Key 形态的字面量
   （拨测绝不允许使用真实模型凭据，M6 §1 拨测约束）；
3. **必须断言线上口径**：`/agent/status` 的 mode / public_mode / usable 与访客工具白名单；
4. **失败必须告警**（开 issue）且零凭据外泄扫描在列。
"""

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "demo-health.yml"
VISITOR_KEY = "visitor-key-please-change"
RETIRED_PUBLIC_KEY = "dev-key-please-change"


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_is_scheduled_and_manual() -> None:
    """定时（自动）与手动触发都要有：定时兜底、手动用于验收。"""
    # YAML 的 `on:` 会被解析成布尔 True（YAML 1.1），两种键名都兼容
    triggers = _workflow().get("on") or _workflow().get(True) or {}
    schedule = triggers.get("schedule") or []
    assert schedule, "拨测没有定时触发（schedule）——线上坏了不会自动发现"
    assert any(entry.get("cron") for entry in schedule), "schedule 缺少 cron 表达式"
    assert "workflow_dispatch" in triggers, "缺少手动触发（验收时要用）"


def test_workflow_uses_public_visitor_key_only() -> None:
    """默认 Key 必须是公开的访客 Key；已退役的演示 Key 只能当"扫描目标"，不能当凭据。"""
    triggers = _workflow().get("on") or _workflow().get(True) or {}
    default_key = triggers["workflow_dispatch"]["inputs"]["api_key"]["default"]
    assert default_key == VISITOR_KEY, "拨测默认 Key 不是公开访客 Key"
    text = _text()
    assert not re.search(rf"X-API-Key:?\s*\"?{RETIRED_PUBLIC_KEY}", text), (
        "拨测把已退役的公开演示 Key 当凭据用了"
    )
    assert not re.search(r"sk-[A-Za-z0-9_\-]{12,}", text), "拨测里出现形似模型 Key 的字面量"


def test_workflow_asserts_public_contract() -> None:
    """断言必须覆盖：能力状态三件套、访客工具白名单、越权 403、公开页面、零外泄扫描。"""
    text = _text()
    for token in (
        "agent_enabled",
        'body["mode"] == "real"',
        'body["public_mode"] == "public"',
        'body["usable"] is True',
        "resume_kb__search_knowledge",  # 访客只见 4 个知识工具
        "/tools/demo_sql__ask/call",  # 越权调用应 403
        "tools-only-key-please-change",  # 能力位（A 红线）在线验证 Key
        'test "$code" = "403"',  # 触发模型被拒
        "api.deepseek.com",  # 零端点外泄扫描
        "线上 5 问复验",  # M6 §4.1 的线上复验（本机无法直连 .vercel.app，靠 Runner 跑）
        "没先检索",  # 复验断言：先检索后作答
        "答案含雇主实名",  # 复验断言：零雇主实名
        "/chat",
        "/resume",
        "/ui",
    ):
        assert token in text, f"拨测缺少断言：{token}"


def test_five_question_recheck_is_manual_only() -> None:
    """5 问复验必须只在手动触发（probe_model=yes）时跑：定时拨测不得天天烧 5 次模型。"""
    steps = _workflow()["jobs"]["accept"]["steps"]
    recheck = next((s for s in steps if "5 问复验" in str(s.get("name"))), None)
    assert recheck is not None, "缺少线上 5 问复验步骤"
    assert recheck.get("if") == "${{ (inputs.probe_model || 'auto') == 'yes' }}", (
        "5 问复验没有做手动门控（会被定时任务反复触发，产生模型成本）"
    )


def test_workflow_probes_real_model_at_most_daily_and_notifies_on_failure() -> None:
    """真模型探测每天最多 1 次（成本红线）+ 失败必须告警（开 issue）。"""
    workflow = _workflow()
    job = workflow["jobs"]["accept"]
    steps = job["steps"]
    probe = next((s for s in steps if s.get("id") == "probe"), None)
    assert probe is not None, "缺少真模型连通性探测步骤"
    # 定时场景下必须按小时门控（只有命中该小时才真打模型）
    assert re.search(r"date -u \+%H", probe["run"]), "真模型探测没有按小时门控，可能每 6 小时都打"
    assert "probe_model" in yaml.safe_dump(probe), "真模型探测缺少手动开关"

    notify = next((s for s in steps if s.get("if") == "failure()"), None)
    assert notify is not None, "失败没有任何告警步骤"
    assert notify["uses"].startswith("actions/github-script@"), "告警实现应使用官方 action"
    # 开 issue 的权限可以声明在 workflow 顶层或 job 上（两种写法都合法）
    permissions = _workflow().get("permissions") or job.get("permissions") or {}
    assert permissions.get("issues") == "write", "告警步骤缺少开 issue 的权限"
