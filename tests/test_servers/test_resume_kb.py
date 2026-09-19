"""resume_kb_server 测试（M3）：知识库加载 / 数据质量 / 语义召回 / 只读工具输出。

数据质量用例把 `knowledge/` 目录本身纳入回归保护——PII 零入、frontmatter 合法、
id 唯一、related 不悬空、雇主名不入库。任何知识库里被误加的联系方式或过期待改
内容都会在这里被拦住（对应执行计划 §5.1 第 1 层「结构性保证」）。
"""

import re
from pathlib import Path

import pytest

from servers.resume_kb_server.kb import (
    KnowledgeBase,
    display_aliases,
    parse_frontmatter,
    tokenize,
)
from servers.resume_kb_server.server import (
    _card_digest,
    _card_full,
    _clamp_top_k,
    get_card,
    get_profile,
    list_cards,
    search_knowledge,
)

KB_ROOT = Path(__file__).resolve().parents[2] / "knowledge"

PII_PATTERNS = {
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"),
    "id_card": re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
}

# 雇主实名：只允许出现在 tags 召回别名里，**正文与下发文本一律不得出现**（T4 裁决）。
# 「海科」用简称即可覆盖「海科新质」（前者是后者的子串），一条断言拦两种写法。
EMPLOYER_NAMES = ("中电福富", "海科")


@pytest.fixture
def kb() -> KnowledgeBase:
    return KnowledgeBase(KB_ROOT)


# ---------------------------------------------------------------------------
# 加载与数据质量
# ---------------------------------------------------------------------------


def test_loads_expected_card_count(kb: KnowledgeBase) -> None:
    """至少 11 张卡片（profile 1 + project 5 + qa 4 + evidence 2 为 M3 约定下限）。"""
    cards = kb.cards
    assert len(cards) >= 11
    types = kb.types()
    assert set(types) == {"profile", "project", "qa", "evidence"}


def test_all_cards_have_required_frontmatter(kb: KnowledgeBase) -> None:
    """每张卡片都要有 type / title / tags / related / updated（供路由与引用）。"""
    for card in kb.cards:
        assert card.type in {"profile", "project", "qa", "evidence"}, card.id
        assert card.title and card.title != card.id, card.id
        assert card.tags, f"{card.id} 缺 tags（语义路由靠它召回）"
        assert isinstance(card.meta.get("related"), list), f"{card.id} 缺 related"
        assert card.meta.get("updated"), f"{card.id} 缺 updated（实测数字需带日期）"


def test_card_ids_unique(kb: KnowledgeBase) -> None:
    """id 全局唯一（related 引用与 get_card 都按 id 定位）。"""
    ids = [card.id for card in kb.cards]
    assert len(ids) == len(set(ids)), f"重复 id：{ids}"


def test_no_pii_in_knowledge_base() -> None:
    """知识库结构性不含 PII：电话 / 邮箱 / 身份证零命中（第 1 层防御的回归断言）。"""
    assert KB_ROOT.is_dir(), "knowledge/ 目录缺失"
    for path in sorted(KB_ROOT.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for name, pattern in PII_PATTERNS.items():
            found = pattern.findall(text)
            assert not found, f"{path.name} 命中 {name}：{found}"


def test_no_employer_names_in_card_body() -> None:
    """雇主名称只允许出现在 tags 召回别名里，**正文一律行业描述**（T4 裁决口径）。

    别名用于命中（面试官可能直接问公司名），正文用于回答——回答里不出现雇主名。
    """
    for path in sorted(KB_ROOT.rglob("*.md")):
        _meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        for name in EMPLOYER_NAMES:
            assert name not in body, f"{path.name} 正文出现雇主名 {name}"


def test_employer_aliases_present_in_tags() -> None:
    """T4 裁决落实：公司名作为召回别名保留在两段实习卡的 tags 里。"""
    cards = {card.id: card for card in KnowledgeBase(KB_ROOT).cards}
    assert "中电福富" in cards["project-internship-medical-saas"].tags
    assert "海科" in cards["project-ocean-nl2sql"].tags


def test_related_references_resolve(kb: KnowledgeBase) -> None:
    """related 引用必须都存在（否则知识图谱断链，引导跳转会落空）。"""
    ids = {card.id for card in kb.cards}
    for card in kb.cards:
        for reference in card.meta.get("related") or []:
            assert reference in ids, f"{card.id} 的 related 悬空：{reference}"


def test_parse_frontmatter_handles_missing_block() -> None:
    """无 frontmatter 的文本原样返回、元数据为空（不抛异常）。"""
    meta, body = parse_frontmatter("纯正文，无元数据")
    assert meta == {}
    assert body == "纯正文，无元数据"


def test_readme_is_not_a_card(kb: KnowledgeBase) -> None:
    """knowledge/README.md 是说明文档，不作为知识卡片加载。"""
    assert all(not card.source.endswith("README.md") for card in kb.cards)


# ---------------------------------------------------------------------------
# 分词与检索
# ---------------------------------------------------------------------------


def test_tokenize_chinese_uses_bigrams() -> None:
    """中文按 2-gram 切分，英文/数字按小写单词切分（与网关 tool_router 同规则）。"""
    tokens = tokenize("语义路由 MCP")
    assert "语义" in tokens and "义路" in tokens and "路由" in tokens
    assert "mcp" in tokens


def test_search_hits_expected_card(kb: KnowledgeBase) -> None:
    """预设面试问题应召回对应卡片（语义路由召回的准入门槛）。"""
    cases = [
        ("这个网关最难的三个坑是什么", "project-mcp-gateway"),
        ("语义路由是怎么实现的", "project-mcp-gateway"),
        ("毕业设计的桌面智能体是怎么工作的", "project-graduation-desktop-agent"),
        ("鸿蒙智能家居的模型微调效果怎么样", "project-harmonyos-smart-home"),
        ("实习期间的随访数据批量导入怎么做的", "project-internship-medical-saas"),
        ("介绍一下你自己", "profile-basic"),
        ("MCP 是什么东西", "qa-mcp-llm"),
        ("限流和缓存一致性怎么做", "qa-storage-infra"),
        ("职业规划和缺点是什么", "qa-hr"),
        ("讲讲你的经历主线", "evidence-main-story"),
        ("你觉得自己匹配这个岗位吗", "evidence-jd-mapping"),
    ]
    for question, expected_id in cases:
        top_ids = [card.id for card in kb.search(question, top_k=3)]
        assert expected_id in top_ids, f"{question!r} 期望命中 {expected_id}，实际 {top_ids}"


def test_search_with_type_filter(kb: KnowledgeBase) -> None:
    """类别过滤只在同类内检索。"""
    results = kb.search("微调", top_k=3, card_type="project")
    assert results
    assert all(card.type == "project" for card in results)


def test_search_falls_back_without_tokens(kb: KnowledgeBase) -> None:
    """查询无有效 token 时保底返回前 top_k（不空手）。"""
    results = kb.search("！？", top_k=2)
    assert len(results) == 2


def test_search_empty_query_type_returns_empty(kb: KnowledgeBase) -> None:
    """top_k<=0 或类别无候选时返回空列表（调用方据此走兜底）。"""
    assert kb.search("网关", top_k=0) == []
    assert kb.search("网关", top_k=3, card_type="不存在的类别") == []


def test_clamp_top_k_bounds() -> None:
    """top_k 收敛到 1..5（入参设界；非法输入回落到默认值）。"""
    assert _clamp_top_k(0) == 1
    assert _clamp_top_k(100) == 5
    assert _clamp_top_k("abc") == 3
    assert _clamp_top_k(2) == 2


# ---------------------------------------------------------------------------
# 只读工具输出
# ---------------------------------------------------------------------------


async def test_search_knowledge_tool_output() -> None:
    """search_knowledge 返回摘要（含 id 与「如需完整内容」提示），不返回全文。"""
    result = await search_knowledge("网关的语义路由", 3)
    assert "project-mcp-gateway" in result
    assert "get_card" in result  # 引导下一步取全文
    assert "对话/思考/响应" not in result  # 全文内容不应出现在摘要里


async def test_get_card_returns_full_body() -> None:
    """get_card 返回完整卡片正文（含 id 与标签头）。"""
    result = await get_card("project-mcp-gateway")
    assert "MCP Gateway" in result
    assert "一句话定位" in result


async def test_get_card_unknown_id_is_graceful() -> None:
    """未知 id 不抛异常，返回可用 id 列表（模型可自纠）。"""
    result = await get_card("no-such-card")
    assert "未找到卡片" in result
    assert "profile-basic" in result


async def test_list_cards_groups_by_type() -> None:
    """list_cards 按类别分组列出全部卡片。"""
    result = await list_cards()
    for card_type in ("profile", "project", "qa", "evidence"):
        assert f"【{card_type}】" in result
    assert "project-mcp-gateway" in result


async def test_get_profile_tool() -> None:
    """get_profile 返回基本信息卡（自我介绍口径与脱敏约定）。"""
    result = await get_profile()
    assert "陈晓伟" in result
    assert "福州理工学院" in result
    assert "自我介绍" in result


# ---------------------------------------------------------------------------
# 召回别名 vs 可展示别名（T4 延伸：别名只用于命中，下发文本零实名）
# ---------------------------------------------------------------------------


def test_display_aliases_drops_employer_names() -> None:
    """过滤保序剔除实名别名；实名作子串的复合别名一并剔除，行业描述不误伤。"""
    assert display_aliases(("实习", "中电福富", "海科新质", "海科", "基层医疗")) == (
        "实习",
        "基层医疗",
    )
    assert display_aliases(("海科新质数据专项",)) == ()
    assert display_aliases(("海洋数据科技", "达梦数据库")) == ("海洋数据科技", "达梦数据库")
    assert display_aliases(()) == ()


def test_display_tags_keep_recall_tags_intact(kb: KnowledgeBase) -> None:
    """两张实习卡：`tags` 保留实名（召回用），`display_tags` 剔除（展示用）。"""
    cards = {card.id: card for card in kb.cards}
    medical = cards["project-internship-medical-saas"]
    assert "中电福富" in medical.tags
    assert "中电福富" not in medical.display_tags
    assert "基层医疗" in medical.display_tags  # 行业描述类别名照常展示

    ocean = cards["project-ocean-nl2sql"]
    assert "海科" in ocean.tags and "海科新质" in ocean.tags
    assert not any("海科" in tag for tag in ocean.display_tags)


def test_card_renderers_never_emit_employer_names() -> None:
    """① 两个渲染出口（摘要行 / 全文头）对**每一张**卡都不得出现雇主实名。

    逐卡遍历而非只查两张实习卡——将来任何卡片把实名写进 tags 都会在此失败，
    等价于给「模型上下文里不可能读到实名」加一道结构性断言。
    """
    cards = KnowledgeBase(KB_ROOT).cards
    assert cards, "knowledge/ 未加载到卡片"
    for card in cards:
        for text in (_card_digest(card), _card_full(card)):
            for name in EMPLOYER_NAMES:
                assert name not in text, f"{card.id} 的下发文本出现雇主实名 {name}"


async def test_tool_outputs_to_model_never_contain_employer_names() -> None:
    """① 端到端：四个只读工具输出里的**知识卡片文本**零雇主实名（模型上下文的唯一来源）。

    刻意不扫的一处：`search_knowledge` 首行会原样回显查询串（`检索「…」命中 N 张卡片：`）。
    面试官在提问里打出的公司名本就在模型上下文里（它就是那条 user 消息），回显不构成
    **新增**泄漏；要保证的是卡片文本不带实名——故此处按行切开分别断言。
    """
    texts = [await list_cards(), await get_profile()]
    for card_id in ("project-internship-medical-saas", "project-ocean-nl2sql"):
        texts.append(await get_card(card_id))
    for query in ("中电福富实习", "海科新质实习", "海科", "实习 随访 海洋数据 NL2SQL"):
        raw = await search_knowledge(query, 5)
        echo, _, card_text = raw.partition("\n")
        assert card_text, f"检索结果应有卡片文本：{raw!r}"
        texts.append(card_text)

    for text in texts:
        assert text, "工具输出不应为空"
        for name in EMPLOYER_NAMES:
            assert name not in text, f"工具下发文本出现雇主实名 {name}"


async def test_search_query_echo_is_the_only_place_the_alias_appears() -> None:
    """实名只随面试官自己的问法回显，卡片文本里一次都不出现（下发的边界说清楚）。"""
    raw = await search_knowledge("中电福富实习", 5)
    echo, _, card_text = raw.partition("\n")
    assert "中电福富" in echo  # 回显用户问法
    assert "中电福富" not in card_text  # 卡片文本零实名
    assert "project-internship-medical-saas" in card_text  # 回收敛：该问法确实召回了目标卡


def test_alias_queries_still_recall_by_employer_name(kb: KnowledgeBase) -> None:
    """② 别名召回不因「过滤展示」而下降：实名/简称问法与同义说法都仍命中对应卡。

    过滤只作用于渲染（display_tags），打分仍用全部 tags——本用例钉住 T4 语义不变。
    """
    cases = [
        ("中电福富实习主要做了什么", "project-internship-medical-saas"),
        ("海科新质那段实习是什么", "project-ocean-nl2sql"),
        ("海科实习做的是什么方向", "project-ocean-nl2sql"),
        # 同义说法（不含实名）：行业描述措辞
        ("央企背景软件服务商的实习做了什么", "project-internship-medical-saas"),
        ("海洋数据自然语言查数专项怎么做的", "project-ocean-nl2sql"),
        # 兜底：只报行业词时也不能召回下降
        ("基层医疗公共卫生的随访怎么做", "project-internship-medical-saas"),
        ("NL2SQL 是怎么保障可用性的", "project-ocean-nl2sql"),
    ]
    for question, expected_id in cases:
        top_ids = [card.id for card in kb.search(question, top_k=3)]
        assert expected_id in top_ids, f"{question!r} 期望命中 {expected_id}，实际 {top_ids}"


# ---------------------------------------------------------------------------
# 网关语义路由的召回验证（工具层）
# ---------------------------------------------------------------------------


async def test_gateway_router_recalls_kb_tools() -> None:
    """网关语义路由在 resume_kb 的 4 个工具中命中期望工具（工具描述可召回）。

    工具描述按中文 2-gram 规则书写，这里用真实 ToolInfo（经 registry 加载，
    非手写替身）过 ToolRouter，验证「描述 → 查询命中」这条链路成立。
    """
    from app.config import MCPServerConfig
    from app.mcp.registry import ToolRegistry
    from app.mcp.tool_router import ToolRouter

    registry = ToolRegistry(
        (
            MCPServerConfig(
                name="resume_kb",
                transport="inprocess",
                module="servers.resume_kb_server.server:server",
            ),
        )
    )
    await registry.connect_all()
    try:
        tools = registry.list_tools()
        assert len(tools) == 4
        router = ToolRouter(top_k=5, min_tools=1)  # min_tools=1 → 4 个工具也走过滤
        cases = [
            ("他的联系方式是什么", "get_profile"),
            ("介绍一下这个人的基本情况", "get_profile"),
            ("都有哪些项目可以介绍", "list_cards"),
            ("项目经历里跟语义路由相关的细节", "search_knowledge"),
        ]
        for question, expected in cases:
            hits = [t.name for t in router.search(question, tools)]
            assert f"resume_kb__{expected}" in hits, f"{question!r} 未召回 {expected}，实际 {hits}"
    finally:
        await registry.close()


async def test_gateway_router_always_returns_kb_tools() -> None:
    """任何问题都不该让知识库工具整体消失在候选之外（保底不空手）。"""
    from app.config import MCPServerConfig
    from app.mcp.registry import ToolRegistry
    from app.mcp.tool_router import ToolRouter

    registry = ToolRegistry(
        (
            MCPServerConfig(
                name="resume_kb",
                transport="inprocess",
                module="servers.resume_kb_server.server:server",
            ),
        )
    )
    await registry.connect_all()
    try:
        tools = registry.list_tools()
        router = ToolRouter(top_k=5, min_tools=1)
        hits = [t.name for t in router.search("完全无关的问题 zzzz", tools)]
        assert hits, "语义路由不应返回空候选"
        assert any(name.startswith("resume_kb__") for name in hits)
    finally:
        await registry.close()
