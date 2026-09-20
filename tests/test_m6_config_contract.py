"""M6 配置契约测试：线上真模型 + 能力位 + 三态开关 + 管理员凭据红线。

四组硬约束（每一条都是"未来某次无意改动会让它失效"的红线，故用测试钉住而不是写在报告里）：

1. **线上配置必须真模型**：`config/gateway.vercel.yaml` 的 `agent` 节 `mock: false` 且指向
   DeepSeek 的 OpenAI 兼容端点——线上简历助手为 real-model-only（决策 6），
   没有 Key 就显示维护页，绝不用 mock 剧本冒充回答；
2. **能力位显式声明（0.5-A）**：该文件里**每一个** Key 都必须显式写 `agent_allowed`，
   除 visitor 外一律 `false` —— 即"公开 Key 即便泄露也零模型成本"；
   并且不得再出现"全量工具又无小时桶"的公开 Key（那正是 M6 要消灭的形态）；
3. **管理员凭据零落盘（0.3）**：任何 YAML 不得出现 `is_admin` 字段、不得出现
   `GATEWAY_ADMIN_KEY` 的值；`auth.admin` 只允许配额字段（白名单式登记）；
4. **三态开关 fail-closed**：配置缺失时默认 `closed`（对外维护），
   线上必须显式写 `public` —— 避免"忘了配 = 静默对外可用"。
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.config import Settings, get_settings
from app.core.mode import (
    ConfigModeStore,
    ModeNotWritableError,
    ProcessModeStore,
    build_mode_store,
)

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
VERCEL_CONFIG = CONFIG_DIR / "gateway.vercel.yaml"
LOCAL_CONFIG = CONFIG_DIR / "gateway.yaml"
VISITOR_KEY = "visitor-key-please-change"
# M6 起已经从线上配置移除的公开演示 Key（保留常量是为了断言"不许回来"）
RETIRED_PUBLIC_KEY = "dev-key-please-change"

# auth.admin 节允许出现的键（配额策略；**凭据字段一律不允许**）
ADMIN_SECTION_ALLOWLIST = {"tenant", "rate_limit_per_minute", "rate_limit_per_hour"}

# 凭据字段名断言的例外（与 tests/test_m5_real_config.py 同口径）：`max_tokens` 是成本
# 护栏字段名，含 "token" 子串但语义与凭据相反。例外集在 m5 那条
# test_credential_field_exception_is_pinned 里被钉死为恰好这一项。
CREDENTIAL_FIELD_EXCEPTIONS = {"max_tokens"}


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# 1. 线上配置：真模型 + 零凭据
# ---------------------------------------------------------------------------


def test_vercel_config_uses_real_model() -> None:
    """线上配置必须是真模型（DeepSeek 兼容端点），这是 M6 的核心切换。"""
    agent = _load(VERCEL_CONFIG)["agent"]
    assert agent["enabled"] is True
    assert agent["mock"] is False, "线上配置必须切真模型（否则 /chat 永远显示维护页）"
    assert agent["model"] == "deepseek-flash"
    assert agent["base_url"].rstrip("/") == "https://api.deepseek.com/v1"
    assert agent["max_rounds"] >= 1  # 成本护栏：轮数有界
    assert agent["max_tokens"] >= 1  # 成本护栏：单次输出有上限（A-02 补齐，线上必须显式设）


def test_online_and_smoke_configs_share_one_model_name() -> None:
    """线上配置与本地冒烟配置的模型名必须一致（否则冒烟结论代表不了线上）。

    2026-09-18 的迁移决策把两处都定为 `deepseek-flash`；这条测试防止"只改一处"。
    """
    online = _load(VERCEL_CONFIG)["agent"]
    smoke = _load(CONFIG_DIR / "gateway.real.yaml")["agent"]
    assert online["model"] == smoke["model"], "线上与冒烟配置的模型名不一致"
    assert online["base_url"].rstrip("/") == smoke["base_url"].rstrip("/")
    # 成本护栏同理：两处取值不同会让"本机冒烟"验证不了线上的输出上限
    assert online["max_tokens"] == smoke["max_tokens"], "线上与冒烟配置的 max_tokens 不一致"


def test_vercel_config_has_no_credential_fields() -> None:
    """线上配置不得出现任何凭据字段：模型 Key 与管理员 Key 都只走环境变量。

    例外：`max_tokens`（成本护栏字段名，含 "token" 子串但语义与凭据相反）。
    与 tests/test_m5_real_config.py 同口径，例外集在那里被钉死为恰好这一项。
    """
    text = VERCEL_CONFIG.read_text(encoding="utf-8")
    agent = _load(VERCEL_CONFIG)["agent"]
    for field in agent:
        if field in CREDENTIAL_FIELD_EXCEPTIONS:
            continue
        assert not re.search(r"(?i)key|token|secret|password", field), field
    # 环境变量名可以出现在注释里（部署说明），但**值**不允许出现任何形似密钥的字面量
    assert not re.search(r"sk-[A-Za-z0-9_\-]{12,}", text), "线上配置里出现了形似模型 Key 的字面量"


# ---------------------------------------------------------------------------
# 2. 能力位（0.5-A）
# ---------------------------------------------------------------------------


def test_vercel_keys_declare_agent_capability_explicitly() -> None:
    """线上每个 Key 都必须显式声明 agent_allowed；除 visitor 外一律 false。

    这条是"公开 Key 即便泄露也烧不到模型余额"的**结构性保证**：新增 Key 时忘了想模型
    成本，CI 会直接红——而不是等 DeepSeek 账单出现异常才发现。
    """
    keys = _load(VERCEL_CONFIG)["auth"]["api_keys"]
    assert keys, "线上配置至少要有 visitor Key"
    for key in keys:
        assert "agent_allowed" in key, f"{key['name']} 未显式声明 agent_allowed"
        if key["key"] == VISITOR_KEY:
            assert key["agent_allowed"] is True
        else:
            assert key["agent_allowed"] is False, f"{key['name']} 不应能触发模型"


def test_vercel_has_tools_only_key_for_online_capability_check() -> None:
    """线上必须保留一把"只能调工具"的验证 Key：能力位红线要能在线上持续验证。

    它的存在使拨测可以每 6 小时断言一次「能调工具 200 / 触发模型 403」，
    而不是只在报告里声明"已实现能力位"。
    """
    keys = _load(VERCEL_CONFIG)["auth"]["api_keys"]
    tools_only = next((k for k in keys if k.get("agent_allowed") is False), None)
    assert tools_only is not None, "线上缺少 agent_allowed=false 的验证 Key"
    assert tools_only["allowed_tools"] == ["resume_kb__*"], "验证 Key 只能读知识库"
    assert tools_only["rate_limit_per_hour"] <= 20, "验证 Key 的小时配额必须有界且很小"
    assert tools_only["key"] != VISITOR_KEY


def test_retired_public_key_is_gone_from_vercel_config() -> None:
    """已泄露的公开演示 Key 不得回到线上配置（0.5-D 的止血效果）。"""
    keys = _load(VERCEL_CONFIG)["auth"]["api_keys"]
    assert RETIRED_PUBLIC_KEY not in {k["key"] for k in keys}
    # 也不能换个名字回来：线上不允许存在"全量工具 + 无小时桶"的 Key
    for key in keys:
        assert key.get("allowed_tools") is not None, f"{key['name']} 是全量工具 Key"
        assert key.get("rate_limit_per_hour"), f"{key['name']} 没有小时桶（突发无法设界）"


def test_local_config_keeps_mock_demo_and_agent_capability() -> None:
    """本地配置必须保持 mock 离线演示可跑（兼容红线），且显式放行访客 Key。"""
    local = _load(LOCAL_CONFIG)
    assert local["agent"]["mock"] is True, "本地配置被改成真模型会破坏离线演示与 CI"
    visitor = next(k for k in local["auth"]["api_keys"] if k["key"] == VISITOR_KEY)
    assert visitor["agent_allowed"] is True
    # 本地演示 Key（mock 用）不设 false：否则 /ui 的 mock 演示会 403
    demo = next(k for k in local["auth"]["api_keys"] if k["key"] == RETIRED_PUBLIC_KEY)
    assert demo.get("agent_allowed") is not False, "本地演示 Key 被禁止触发 Agent，会破坏 mock 演示"


# ---------------------------------------------------------------------------
# 3. 管理员凭据零落盘（0.3）
# ---------------------------------------------------------------------------


def test_no_config_declares_is_admin() -> None:
    """任何 YAML 都不得声明 is_admin —— 管理员身份只能来自环境变量。

    否则任何人往仓库提一个 `is_admin: true` 的 Key 就等于拿到管理权限。
    """
    offenders = [
        f"{path.name}:{key['name']}"
        for path in sorted(CONFIG_DIR.glob("*.yaml"))
        for key in (_load(path).get("auth", {}) or {}).get("api_keys", [])
        if "is_admin" in key
    ]
    assert not offenders, (
        "配置里出现 is_admin 字段（管理员身份只允许来自环境变量）：\n" + "\n".join(offenders)
    )


def test_admin_section_only_holds_quota_fields() -> None:
    """auth.admin 只允许配额字段：这里永远不放管理员 Key 值。"""
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        admin = ((_load(path).get("auth", {}) or {}).get("admin")) or {}
        extra = set(admin) - ADMIN_SECTION_ALLOWLIST
        assert not extra, f"{path.name} 的 auth.admin 出现未登记字段：{sorted(extra)}"
        assert not re.search(r"(?i)key|token|secret|password", " ".join(admin)), path.name


def test_admin_key_env_name_is_not_hardcoded_as_value_anywhere() -> None:
    """环境变量名可以出现在文档/代码里，但不得有"变量名 = 值"形式的赋值出现在配置中。"""
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"GATEWAY_ADMIN_KEY\s*[:=]\s*\S+", text), path.name


# ---------------------------------------------------------------------------
# 4. 三态开关：fail-closed 默认 + 线上显式 public
# ---------------------------------------------------------------------------


def test_public_mode_defaults_to_closed() -> None:
    """未配置时开关默认 closed（fail-closed）、存储默认只读——配置缺失应表现为"对外不可用"。"""
    assert Settings.model_fields["public_mode"].default == "closed"
    assert Settings.model_fields["public_mode_store"].default == "config"


def test_platform_configs_pin_public_mode_and_store() -> None:
    """三套平台配置都必须显式写开关与存储形态（避免"默认值悄悄变化"）。"""
    expected = {
        "gateway.yaml": "process",  # 本机单进程：可就地切换
        "gateway.docker.yaml": "process",  # 容器单进程：可就地切换
        "gateway.vercel.yaml": "config",  # 多实例 Serverless：只读，改配置+重新部署
    }
    for name, store in expected.items():
        gateway = _load(CONFIG_DIR / name)["gateway"]
        assert gateway["public_mode"] == "public", f"{name} 必须显式开 public"
        assert gateway["public_mode_store"] == store, name


def test_mode_store_writability_boundaries() -> None:
    """只读实现拒绝写入并给出确定性指引；进程内实现可就地切换。"""
    readonly = ConfigModeStore("public", "yaml")
    assert readonly.writable is False
    assert readonly.get() == "public"
    with pytest.raises(ModeNotWritableError) as excinfo:
        readonly.set("closed")
    assert "新部署" in str(excinfo.value)  # 指引必须说清确定性路径

    writable = ProcessModeStore("public")
    assert writable.writable is True
    writable.set("closed")
    assert writable.get() == "closed"
    assert writable.source == "process"


def test_build_mode_store_follows_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_mode_store 按 public_mode_store 配置选实现（默认 config=只读）。"""
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(VERCEL_CONFIG))
    get_settings.cache_clear()
    store = build_mode_store()
    assert store.writable is False  # 线上：只读（多实例无共享状态）
    assert store.get() == "public"

    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(LOCAL_CONFIG))
    get_settings.cache_clear()
    local_store = build_mode_store()
    assert local_store.writable is True  # 本机：可写
