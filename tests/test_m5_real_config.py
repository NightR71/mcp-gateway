"""M5 配置契约测试：真模型冒烟配置的硬约束 + 凭据零落盘 + mock 路径不回退。

三个目的：
1. **凭据零落盘（安全红线）**：仓库内任何 YAML 都不得出现 LLM 提供方 Key 或
   形似密钥的字面量——Key 唯一来源是环境变量 `GATEWAY_AGENT_API_KEY`。
   这是可以被后续任何人无意破坏的红线，故用测试永久钉住，而不是只在报告里写一句。
2. **真模型配置可用**：`config/gateway.real.yaml` 能被配置中心原样加载
   （`agent` 节只走 YAML，见 M5 已知坑），且指向 DeepSeek 的 OpenAI 兼容端点。
3. **两条路径并存**：`config/gateway.yaml` 必须保持 mock 离线演示能力，
   新增 real 配置不得把它改成真模型（否则本地演示与 CI 都会要 Key）。
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.config import get_agent_config, get_server_configs

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
LOCAL_CONFIG = CONFIG_DIR / "gateway.yaml"
REAL_CONFIG = CONFIG_DIR / "gateway.real.yaml"
VISITOR_KEY = "visitor-key-please-change"

# 形似 LLM 提供方密钥的字面量（OpenAI/DeepSeek 系 `sk-`、Bearer 头、赋值式密钥字段）。
# 不匹配网关自身的演示 API Key 字面量（如 `dev-key-please-change`）——
# 那些是鉴权演示值、本身公开，与「绝不下盘的模型提供方 Key」是两回事。
CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),  # OpenAI / DeepSeek Key 前缀
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),  # Authorization 头
    re.compile(r"(?i)(agent|llm|openai|deepseek)[_-]?api[_-]?key\s*[:=]\s*\S+"),
    re.compile(r"(?i)(api[_-]?key|secret|password)\s*[:=]\s*(?!please-change)\S{12,}"),
)

# agent 节允许出现的键（白名单式：新增字段必须显式在此登记，防止偷偷加 key 字段）
AGENT_FIELD_ALLOWLIST = {
    "enabled",
    "mock",
    "model",
    "base_url",
    "max_rounds",
    "routing_top_k",
}


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _walk(node: Any, path: str = "") -> list[tuple[str, Any]]:
    """递归展开 YAML 节点为 (路径, 标量值) 列表，用于全量凭据扫描。"""
    items: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            items.extend(_walk(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            items.extend(_walk(value, f"{path}[{index}]"))
    else:
        items.append((path, node))
    return items


# ---------------------------------------------------------------------------
# 安全红线：配置目录内零凭据
# ---------------------------------------------------------------------------


def test_no_credential_literals_in_any_config() -> None:
    """config/ 下任何 YAML 都不得出现模型提供方 Key 或形似密钥的字面量。"""
    offenders: list[str] = []
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        for node_path, value in _walk(_load(path)):
            if not isinstance(value, str):
                continue
            for pattern in CREDENTIAL_VALUE_PATTERNS:
                if pattern.search(value):
                    offenders.append(f"{path.name}:{node_path} 命中 {pattern.pattern}")
    assert not offenders, "配置文件中出现疑似凭据字面量：\n" + "\n".join(offenders)


def test_agent_section_has_no_credential_field() -> None:
    """agent 节只允许登记白名单内的键，且不含任何以 key/token/secret 命名的字段。"""
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        agent = _load(path).get("agent")
        if agent is None:
            continue
        assert set(agent) <= AGENT_FIELD_ALLOWLIST, f"{path.name} 的 agent 节含未登记字段"
        for field in agent:
            assert not re.search(r"(?i)key|token|secret|password", field), path.name


# ---------------------------------------------------------------------------
# 真模型配置：可被配置中心加载，且指向 DeepSeek
# ---------------------------------------------------------------------------


def test_real_config_switches_agent_to_real_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """真模型配置必须能被配置中心原样读成 mock=false + DeepSeek 端点。

    直接走 `get_agent_config()` 而不是手写 yaml 断言：M5 已知坑正是
    「agent 节只从 YAML 读」，只有经过真实读取路径才能证明切得动。
    """
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(REAL_CONFIG))
    config = get_agent_config()
    assert config.enabled is True
    assert config.mock is False, "真模型配置的 mock 必须为 false"
    assert config.model == "deepseek-flash"
    assert config.base_url.rstrip("/") == "https://api.deepseek.com/v1"
    assert config.max_rounds >= 1  # 成本护栏：轮数与工具注入数都须有界


def test_real_config_declares_knowledge_base_and_demo_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真模型配置的 servers 与本地配置逐字一致：知识库在、白名单对比面也在。"""
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(REAL_CONFIG))
    real_servers = {s.name: s for s in get_server_configs()}
    local_servers = _load(LOCAL_CONFIG)["servers"]
    assert (
        {s.name for s in real_servers.values()}
        == {s["name"] for s in local_servers}
        == {"demo_sql", "resume_kb"}
    )
    # 两套配置除 agent 节外逐字一致：真模型冒烟不引入额外的 server 差异
    assert _load(REAL_CONFIG)["servers"] == local_servers
    # resume_kb 必须 inprocess（Serverless 友好）+ 指向只读知识库 Server
    assert real_servers["resume_kb"].transport == "inprocess"
    assert real_servers["resume_kb"].module == "servers.resume_kb_server.server:server"


def test_real_config_visitor_key_scope_mirrors_local() -> None:
    """真模型配置的访客 Key 权限与本地配置一致（真模型下白名单不得放宽）。"""
    real_keys = _load(REAL_CONFIG)["auth"]["api_keys"]
    local_keys = _load(LOCAL_CONFIG)["auth"]["api_keys"]
    real_visitor = next(k for k in real_keys if k["key"] == VISITOR_KEY)
    local_visitor = next(k for k in local_keys if k["key"] == VISITOR_KEY)
    assert real_visitor == local_visitor, "真模型配置放大了访客 Key 的权限"
    assert real_visitor["allowed_tools"] == ["resume_kb__*"]
    assert real_visitor["rate_limit_per_hour"] == 50


def test_iteration_key_cannot_escalate_beyond_visitor() -> None:
    """冒烟迭代 Key（放开配额的那个）权限必须与访客 Key 逐字相同，只许改配额。

    否则「为了方便连测」就会悄悄变成一个能调 demo_sql 的宽权限 Key。
    """
    keys = _load(REAL_CONFIG)["auth"]["api_keys"]
    visitor = next(k for k in keys if k["key"] == VISITOR_KEY)
    smoke = next(k for k in keys if k["key"] == "smoke-key-local-only")
    assert smoke["allowed_tools"] == visitor["allowed_tools"] == ["resume_kb__*"]
    assert smoke["tenant"] == "smoke"  # 独立租户，便于观测/停用
    assert smoke["rate_limit_per_hour"] >= visitor["rate_limit_per_hour"]


def test_knowledge_tools_are_never_filtered_on_the_agent_path() -> None:
    """真模型/生产配置下，访客可见的 4 个知识库工具必须**全部注入** Agent。

    ToolRouter 的判据是 `工具总数 <= min_tools 则不过滤`。知识库 Agent 需要
    search → get_card/get_profile 的完整工具集才能读到卡片全文；一旦被过滤成
    「只注入 search_knowledge」，模型只能凭一眼摘要作答（M5 配置核对时实测踩到：
    照抄演示配置的 min_tools=3 就会这样）。这里按行为判据钉住，防止改回去。
    """
    kb_tools = 4  # resume_kb server 的只读工具数
    for path in (REAL_CONFIG, CONFIG_DIR / "gateway.vercel.yaml"):
        routing = _load(path).get("routing") or {}
        min_tools = routing.get("min_tools", 10)  # 缺省 10（ToolRouterConfig 默认值）
        top_k = routing.get("top_k", 10)
        assert routing.get("enabled", True) is True, f"{path.name} 语义路由被关掉了"
        assert min_tools >= kb_tools, (
            f"{path.name} 的 routing.min_tools={min_tools} < {kb_tools}："
            "知识库工具会被过滤，模型读不到卡片全文"
        )
        assert top_k >= kb_tools, f"{path.name} 的 routing.top_k={top_k} 放不下知识工具"


# ---------------------------------------------------------------------------
# 兼容：mock 离线演示路径不得回退
# ---------------------------------------------------------------------------


def test_production_persona_forbids_process_narration() -> None:
    """生产人设必须禁止过程性旁白/英文自述（M5 §6.6 实测补的规则）。

    背景：真模型冒烟发现 52% 的回答开头出现「I'll search the knowledge base for relevant
    details.」——决策轮的旁白被流式下发给了面试官。该规则是修复的一部分，不能被悄悄删掉。
    """
    rules = _load(CONFIG_DIR / "resume_kb.yaml")["persona"]["rules"]
    narration_rules = [r for r in rules if "过程性旁白" in r or "思考过程" in r]
    assert narration_rules, "人设里缺少「禁止过程性旁白」规则——英文旁白会重新出现"
    joined = "\n".join(narration_rules)
    assert "中文" in joined, "该规则应同时要求全程中文"
    # 规则里要给出具体反例（实测表明：只写抽象禁令时仍有 9% 漏网）
    assert "I'll" in joined, "规则应包含具体反例（实测抽象禁令不足以稳定约束模型）"


def test_local_config_stays_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认配置（config/gateway.yaml）必须保持 mock，离线演示与 CI 都不需要 Key。"""
    assert _load(LOCAL_CONFIG)["agent"]["mock"] is True
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(LOCAL_CONFIG))
    config = get_agent_config()
    assert config.mock is True
    assert config.enabled is True


def test_docker_config_does_not_enable_real_model() -> None:
    """docker 平台配置不得切真模型（那会让容器化部署无 Key 直接不可用）。

    docker 配置目前没有 agent 节（该平台不暴露 Agent），本用例对「缺节」放行、
    只拦「显式配了真模型」——这样以后有人补 agent 节时不会踩到真模型默认值。
    **M6 变更**：vercel 平台配置已正式切真模型（线上简历助手需要），原「docker/vercel
    都不许切真模型」的断言随之拆成两条——本用例只守 docker；vercel 的护栏换成更强的
    「必须真模型 + 零凭据 + 能力位显式声明」，见 tests/test_m6_config_contract.py。
    """
    agent = _load(CONFIG_DIR / "gateway.docker.yaml").get("agent")
    if agent is None:
        return
    assert agent.get("mock") is True, "gateway.docker.yaml 不应启用真模型"
    assert "deepseek" not in str(agent.get("base_url", "")).lower()
