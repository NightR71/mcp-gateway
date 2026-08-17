"""NL2SQL 规则引擎单元测试：规则命中、SQL 只读性、兜底。"""

import pytest

from servers.demo_sql_server.db import validate_readonly
from servers.demo_sql_server.nl2sql import SUPPORTED_EXAMPLES, question_to_sql


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
