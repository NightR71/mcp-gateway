"""Semantic Tool Routing（阶段 1）单元测试：分词 / 建索引 / 打分 / 路由行为。

全部为纯本地测试（零子进程、零网络），构造合成 ToolInfo 模拟 demo server 工具。
"""

from app.mcp.schemas import ToolInfo
from app.mcp.tool_router import (
    KeywordScorer,
    ToolDocument,
    ToolRouter,
    build_documents,
    tokenize,
)


def _tool(
    name: str,
    description: str = "",
    schema_properties: dict | None = None,
    server: str = "demo_sql",
) -> ToolInfo:
    return ToolInfo(
        name=name,
        original_name=name.split("__", 1)[-1],
        server=server,
        description=description,
        input_schema={"type": "object", "properties": schema_properties or {}},
    )


DEMO_TOOLS = [
    _tool(
        "demo_sql__ask",
        "用中文自然语言提问，自动生成并执行 SQL 返回结果。支持销售额、客户数、订单量等业务查询",
        {"question": {"type": "string", "description": "自然语言问题"}},
    ),
    _tool("demo_sql__echo", "回显输入的消息（链路调试用）", {"message": {"type": "string"}}),
    _tool("demo_sql__run_sql", "直接执行只读 SQL，返回 Markdown 表格", {"sql": {"type": "string"}}),
    _tool("demo_sql__list_tables", "列出演示库全部业务表的建表语句", {}),
]


# ---------- 分词 ----------


def test_tokenize_ascii_runs_lowercased() -> None:
    tokens = tokenize("Demo_SQL__Ask 2024")
    assert "demo_sql__ask" in tokens  # 完整 run 保留
    assert "demo" in tokens and "sql" in tokens and "ask" in tokens  # 下划线分段补充
    assert "2024" in tokens
    assert all(t == t.lower() for t in tokens)


def test_tokenize_chinese_bigrams() -> None:
    assert tokenize("销售额") == ["销售", "售额"]
    assert tokenize("查询销售额") == ["查询", "询销", "销售", "售额"]


def test_tokenize_ignores_punctuation_and_whitespace() -> None:
    tokens = tokenize("查询销售额最高的商品？")
    assert "？" not in tokens
    assert "查询" in tokens and "销售" in tokens and "售额" in tokens


# ---------- 建索引 ----------


def test_build_documents_separates_fields() -> None:
    docs = build_documents(DEMO_TOOLS)
    assert len(docs) == 4
    ask_doc = next(d for d in docs if d.tool.name == "demo_sql__ask")
    assert "ask" in ask_doc.field_tokens["name"]
    assert "销售" in ask_doc.field_tokens["description"]
    assert "问题" in ask_doc.field_tokens["schema"]
    assert ask_doc.searchable_text  # 拼接文本非空


# ---------- 打分 ----------


def test_keyword_scorer_name_outranks_description() -> None:
    scorer = KeywordScorer()
    name_doc = ToolDocument(
        tool=_tool("foo__sales_report", "销售报表"),
        searchable_text="",
        field_tokens={"name": {"sales"}, "description": {"销售"}, "schema": set()},
    )
    desc_doc = ToolDocument(
        tool=_tool("foo__report", "sales 报表"),
        searchable_text="",
        field_tokens={"name": {"report"}, "description": {"sales"}, "schema": set()},
    )
    assert scorer.score(["sales"], name_doc) > scorer.score(["sales"], desc_doc)


def test_keyword_scorer_description_outranks_schema() -> None:
    scorer = KeywordScorer()
    desc_doc = ToolDocument(
        tool=_tool("foo__a"),
        searchable_text="",
        field_tokens={"name": set(), "description": {"销售"}, "schema": set()},
    )
    schema_doc = ToolDocument(
        tool=_tool("foo__b"),
        searchable_text="",
        field_tokens={"name": set(), "description": set(), "schema": {"销售"}},
    )
    assert scorer.score(["销售"], desc_doc) > scorer.score(["销售"], schema_doc)


# ---------- ToolRouter ----------


def test_search_ranks_sales_tool_first() -> None:
    """「销售额」命中 ask 高于 echo（关键词语义排序）。"""
    router = ToolRouter(top_k=10, min_tools=2)
    result = router.search("销售额", DEMO_TOOLS)
    assert result[0].name == "demo_sql__ask"
    assert "demo_sql__ask" in {t.name for t in result}
    assert "demo_sql__echo" not in {t.name for t in result}


def test_search_top_k_truncation() -> None:
    """命中后按 top_k 截断。"""
    router = ToolRouter(top_k=1, min_tools=2)
    result = router.search("Markdown", DEMO_TOOLS)
    assert len(result) == 1
    assert result[0].name == "demo_sql__run_sql"  # 唯一命中 description 的工具


def test_search_min_tools_no_filter() -> None:
    """工具总数 ≤ min_tools 时原样返回全部（顺序不变）。"""
    router = ToolRouter(top_k=2, min_tools=10)
    result = router.search("销售额", DEMO_TOOLS)
    assert [t.name for t in result] == [t.name for t in DEMO_TOOLS]


def test_search_no_hit_returns_fallback() -> None:
    """query 无命中时返回原列表前 top_k（保底，不空手）。"""
    router = ToolRouter(top_k=2, min_tools=2)
    result = router.search("完全不存在的关键词xyz", DEMO_TOOLS)
    assert [t.name for t in result] == [t.name for t in DEMO_TOOLS[:2]]


def test_search_empty_query_returns_first_top_k() -> None:
    router = ToolRouter(top_k=2, min_tools=2)
    result = router.search("", DEMO_TOOLS)
    assert [t.name for t in result] == [t.name for t in DEMO_TOOLS[:2]]


def test_search_top_k_param_overrides_default() -> None:
    router = ToolRouter(top_k=10, min_tools=2)
    result = router.search("sql", DEMO_TOOLS, top_k=1)
    assert len(result) == 1


def test_search_custom_scorer_injected() -> None:
    """Scorer Protocol 预留扩展点：可注入自定义打分实现。"""

    class FixedScorer:
        def score(self, query_tokens: list[str], doc: ToolDocument) -> float:
            return 9.0 if doc.tool.name == "demo_sql__echo" else 1.0

    router = ToolRouter(top_k=10, min_tools=2, scorer=FixedScorer())
    result = router.search("任意", DEMO_TOOLS)
    assert result[0].name == "demo_sql__echo"
