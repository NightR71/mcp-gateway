"""M3 配置契约测试：生产配置的硬约束 + 测试夹具与生产配置不漂移。

两个目的：
1. **生产配置不可回退**：访客 Key 只能读简历知识库、三套平台配置都必须能加载
   resume_kb（否则部署到该平台时知识库直接缺席）、人设与守门必须开启；
2. **防漂移**：测试夹具 `resume_kb_test.yaml` 是生产 `resume_kb.yaml` 的精简副本——
   若只改生产、忘改夹具，测试会「全绿但失真」。这里断言两者的**结构键**一致
   （策略 id / 话术 key / 打码模式名），文案允许不同。
"""

from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
FIXTURE_DIR = Path(__file__).parent / "fixtures"

PRODUCTION_KEYS = CONFIG_DIR / "gateway.yaml"
PLATFORM_CONFIGS = (
    CONFIG_DIR / "gateway.yaml",
    CONFIG_DIR / "gateway.docker.yaml",
    CONFIG_DIR / "gateway.vercel.yaml",
)

VISITOR_KEY = "visitor-key-please-change"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# 访客 Key 契约（生产配置）
# ---------------------------------------------------------------------------


def test_visitor_key_is_kb_only_with_hourly_quota() -> None:
    """生产配置的访客 Key：白名单仅 resume_kb__*、小时桶 50、分钟桶小额。"""
    keys = _load(PRODUCTION_KEYS)["auth"]["api_keys"]
    visitor = next((k for k in keys if k["key"] == VISITOR_KEY), None)
    assert visitor is not None, "生产配置缺少 visitor Key"
    assert visitor["allowed_tools"] == ["resume_kb__*"]
    assert visitor["rate_limit_per_hour"] == 50  # 执行计划 §2 决策 7
    assert visitor["rate_limit_per_minute"] <= 10  # 防单点突发
    assert visitor.get("tenant") == "visitor"


def test_visitor_key_has_no_wildcard_beyond_kb() -> None:
    """白名单不得出现能覆盖其他 server 的通配（如 `*` 或 `demo_sql__*`）。"""
    keys = _load(PRODUCTION_KEYS)["auth"]["api_keys"]
    visitor = next(k for k in keys if k["key"] == VISITOR_KEY)
    for entry in visitor["allowed_tools"]:
        assert entry.startswith("resume_kb__"), entry
        assert entry != "*"


# ---------------------------------------------------------------------------
# 三套平台配置都可加载简历知识库
# ---------------------------------------------------------------------------


def test_all_platform_configs_declare_resume_kb_inprocess() -> None:
    """本地/Docker/Vercel 三套配置都必须声明 resume_kb 且走 inprocess。

    docker 与 Vercel 尤其重要：前者靠 Dockerfile 的 COPY knowledge 供数据，
    后者是 Serverless（拉不起子进程）——漏一处该平台的知识库就整体缺席。
    """
    for path in PLATFORM_CONFIGS:
        servers = {s["name"]: s for s in _load(path)["servers"]}
        assert "resume_kb" in servers, f"{path.name} 未声明 resume_kb"
        assert servers["resume_kb"]["transport"] == "inprocess", path.name
        assert servers["resume_kb"]["module"] == "servers.resume_kb_server.server:server", path.name


# ---------------------------------------------------------------------------
# 生产应用层配置：人设与守门必须开启
# ---------------------------------------------------------------------------


def test_production_persona_and_guard_enabled() -> None:
    """生产 resume_kb.yaml：人设与输出守门开启，且关键内容齐备。"""
    config = _load(CONFIG_DIR / "resume_kb.yaml")
    persona = config["persona"]
    assert persona["enabled"] is True
    assert persona["rules"] and persona["redlines"] and persona["fallbacks"]
    assert persona["reply_policies"], "缺少固定回应策略"
    for policy in persona["reply_policies"]:
        assert policy["reply_key"] in persona["fallbacks"], policy["id"]

    guard = config["output_guard"]
    assert guard["enabled"] is True
    names = {p["name"] for p in guard["patterns"]}
    assert {"phone", "email"} <= names, "最低要求：手机号与邮箱必须打码"
    # 窗口须不小于最长模式的实际长度（手机号 11 / 身份证 18），否则跨 token 会漏
    assert guard["window_chars"] >= 18


def test_persona_fallbacks_cover_redlines() -> None:
    """T 清单落地检查：成绩/联系方式/仓库/薪资四条固定话术必须存在。"""
    persona = _load(CONFIG_DIR / "resume_kb.yaml")["persona"]
    for key in ("grade_boundary", "guide_to_resume", "repo_boundary", "salary_boundary"):
        assert key in persona["fallbacks"], key
    assert "一辩 91 分" in persona["fallbacks"]["grade_boundary"]  # T1 已定口径


# ---------------------------------------------------------------------------
# 测试夹具 vs 生产配置：结构不漂移
# ---------------------------------------------------------------------------


def test_fixture_matches_production_structure() -> None:
    """夹具与生产配置的策略/话术/模式键集合一致（文案可精简，结构不可缺）。"""
    prod = _load(CONFIG_DIR / "resume_kb.yaml")
    fixture = _load(FIXTURE_DIR / "resume_kb_test.yaml")

    prod_policies = [p["id"] for p in prod["persona"]["reply_policies"]]
    fixture_policies = [p["id"] for p in fixture["persona"]["reply_policies"]]
    assert fixture_policies == prod_policies, "固定回应策略列表与生产不一致"

    assert set(fixture["persona"]["fallbacks"]) == set(prod["persona"]["fallbacks"])
    assert {p["name"] for p in fixture["output_guard"]["patterns"]} == {
        p["name"] for p in prod["output_guard"]["patterns"]
    }


def test_fixture_visitor_keys_mirror_production() -> None:
    """夹具的访客 Key 白名单口径与生产一致（否则测试测的不是真实权限模型）。"""
    fixture = _load(FIXTURE_DIR / "gateway_resume_kb_test.yaml")
    visitors = [k for k in fixture["auth"]["api_keys"] if k.get("tenant") == "visitor"]
    assert visitors, "夹具缺少访客系 Key"
    for key in visitors:
        assert key["allowed_tools"] == ["resume_kb__*"]
