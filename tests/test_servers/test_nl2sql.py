"""NL2SQL 规则引擎单元测试：规则命中、SQL 只读性、兜底（阶段 5 追加双引擎测试）。"""

import pytest

from servers.demo_sql_server.db import validate_readonly
from servers.demo_sql_server.nl2sql import (
    ENGINE_LABELS,
    SUPPORTED_EXAMPLES,
    HybridTranslator,
    RuleTranslator,
    Translation,
    create_translator,
    question_to_sql,
    translate,
)


@pytest.mark.parametrize(
    "question",
    [
        "有多少客户？",
        "各城市的客户分布",
        "金牌客户有多少？",
        "一共有多少订单？",
        "订单状态分布如何？",
        "总销售额是多少？",
        "平均订单金额是多少？",
        "最畅销的产品是什么？",
        "各产品的销售额排行",
        "最近 5 笔订单",
        "哪些产品库存不足需要补货？",
        "我们有哪些产品？",
        "消费最多的大客户是谁？",
    ],
)
def test_common_questions_hit_rules(question: str) -> None:
    """常见演示问题都应命中规则，且生成的 SQL 能通过只读校验。"""
    sql = question_to_sql(question)
    assert sql is not None, f"未命中规则: {question}"
    assert validate_readonly(sql) == sql


def test_unknown_question_returns_none() -> None:
    assert question_to_sql("今天天气怎么样？") is None


def test_examples_all_supported() -> None:
    """兜底文案里展示的每个示例问题都必须能命中规则（自洽性）。"""
    assert SUPPORTED_EXAMPLES
    for example in SUPPORTED_EXAMPLES:
        assert question_to_sql(example) is not None, f"示例问题未命中: {example}"


# ---------- 阶段 5：双引擎（rule / llm / hybrid） ----------


class _FakeLLM:
    """可注入的假 LLM 翻译器（记录调用，返回固定 SQL）。"""

    def __init__(self, sql: str | None = "SELECT 1 AS ok") -> None:
        self.sql = sql
        self.calls: list[str] = []

    async def translate(self, question: str, schema_text: str) -> Translation:
        self.calls.append(question)
        return Translation(sql=self.sql, engine="llm" if self.sql else "none")


def test_create_translator_three_modes() -> None:
    """三模式选择正确：rule → RuleTranslator；llm → 注入的 llm；hybrid → HybridTranslator。"""
    assert isinstance(create_translator("rule"), RuleTranslator)
    llm = _FakeLLM()
    assert create_translator("llm", llm=llm) is llm
    assert isinstance(create_translator("hybrid", llm=llm), HybridTranslator)


def test_create_translator_llm_requires_llm() -> None:
    """llm 模式必须提供 llm，否则 ValueError。"""
    with pytest.raises(ValueError, match="llm"):
        create_translator("llm")


def test_create_translator_hybrid_degrades_without_llm() -> None:
    """hybrid 无 LLM 配置自动退化为 rule（行为与一阶段一致）。"""
    assert isinstance(create_translator("hybrid"), RuleTranslator)


async def test_hybrid_rule_hit_does_not_call_llm() -> None:
    """hybrid 规则命中优先，不调用 LLM。"""
    llm = _FakeLLM()
    translator = create_translator("hybrid", llm=llm)
    result = await translator.translate("总销售额是多少？", "")
    assert result.engine == "rule"
    assert "SUM(amount)" in result.sql
    assert llm.calls == []  # LLM 未被调用


async def test_hybrid_rule_miss_falls_back_to_llm() -> None:
    """hybrid 规则未命中降级 LLM，且结果标注引擎 llm。"""
    llm = _FakeLLM(sql="SELECT name FROM products WHERE stock < 10")
    translator = create_translator("hybrid", llm=llm)
    result = await translator.translate(
        "上个月的退货率是多少？", "CREATE TABLE products (name TEXT, stock INT)"
    )
    assert result.engine == "llm"
    assert result.sql == "SELECT name FROM products WHERE stock < 10"
    assert llm.calls == ["上个月的退货率是多少？"]


async def test_hybrid_llm_failure_returns_none() -> None:
    """hybrid 规则未命中且 LLM 失败（返回 None）时整体 None，不抛异常。"""
    llm = _FakeLLM(sql=None)
    translator = create_translator("hybrid", llm=llm)
    result = await translator.translate("今天天气如何？", "")
    assert result.sql is None
    assert result.engine == "none"


async def test_module_level_translate_convenience() -> None:
    """模块级 translate() 便捷入口：默认 rule 模式。"""
    result = await translate("总销售额是多少？", "CREATE TABLE orders (amount REAL)")
    assert result.engine == "rule"
    assert "SUM(amount)" in result.sql


def test_engine_labels() -> None:
    """引擎展示名映射：结果文本「引擎：规则 / LLM」。"""
    assert ENGINE_LABELS["rule"] == "规则"
    assert ENGINE_LABELS["llm"] == "LLM"
