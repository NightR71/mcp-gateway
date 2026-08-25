"""NL2SQL 双引擎（阶段 5）：规则模板兜底 + 可选 LLM 增强。

- `question_to_sql()`：规则引擎（原样保留，一阶段行为不变）；
- Translator Protocol + `create_translator(mode)`：rule / llm / hybrid 三模式；
  hybrid 规则命中优先，未命中才降级 LLM；无 LLM 配置时自动退化为 rule。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

# (匹配正则, SQL 模板)，按声明顺序优先命中
_RULES: tuple[tuple[str, str], ...] = (
    (
        r"(客户|用户).*(多少|数量|总数|几个)|多少.*(客户|用户)",
        "SELECT COUNT(*) AS 客户总数 FROM customers",
    ),
    (
        r"客户.*(城市|分布)|城市.*客户|各城市",
        "SELECT city AS 城市, COUNT(*) AS 客户数 FROM customers GROUP BY city ORDER BY 客户数 DESC",
    ),
    (
        r"(vip|VIP|高价值|金牌).*客户|客户.*(等级|vip|VIP)",
        "SELECT vip_level AS 会员等级, COUNT(*) AS 客户数 FROM customers "
        "GROUP BY vip_level ORDER BY 客户数 DESC",
    ),
    (
        r"订单.*(多少|数量|总数|几笔)|多少.*订单",
        "SELECT COUNT(*) AS 订单总数 FROM orders",
    ),
    (
        r"订单状态|状态.*(分布|统计)",
        "SELECT status AS 订单状态, COUNT(*) AS 订单数 FROM orders GROUP BY status "
        "ORDER BY 订单数 DESC",
    ),
    (
        r"总销售|销售(总)?额|营收|营业额|GMV|gmv",
        "SELECT ROUND(SUM(amount), 2) AS 总销售额 FROM orders WHERE status = '已完成'",
    ),
    (
        r"平均.*(订单|单笔)|客单价",
        "SELECT ROUND(AVG(amount), 2) AS 平均订单金额 FROM orders WHERE status = '已完成'",
    ),
    (
        r"最畅销|销量最高|销售额最高|卖得最好|热门(产品|商品)",
        "SELECT p.name AS 产品, ROUND(SUM(o.amount), 2) AS 销售额, SUM(o.quantity) AS 销量 "
        "FROM orders o JOIN products p ON o.product_id = p.id "
        "WHERE o.status = '已完成' GROUP BY p.id ORDER BY 销售额 DESC LIMIT 1",
    ),
    (
        r"各产品|每个产品|产品.*(销售额|销量|排行|排名)|(销售额|销量).*排行",
        "SELECT p.name AS 产品, ROUND(SUM(o.amount), 2) AS 销售额, SUM(o.quantity) AS 销量 "
        "FROM orders o JOIN products p ON o.product_id = p.id "
        "WHERE o.status = '已完成' GROUP BY p.id ORDER BY 销售额 DESC",
    ),
    (
        r"最近|最新.*订单|近期订单",
        "SELECT o.id AS 订单号, c.name AS 客户, p.name AS 产品, o.amount AS 金额, "
        "o.status AS 状态, o.created_at AS 下单时间 FROM orders o "
        "JOIN customers c ON o.customer_id = c.id JOIN products p ON o.product_id = p.id "
        "ORDER BY o.created_at DESC LIMIT 5",
    ),
    (
        r"库存.*(不足|最少|最低|紧张)|缺货|补货",
        "SELECT name AS 产品, stock AS 当前库存 FROM products ORDER BY stock ASC LIMIT 5",
    ),
    (
        r"产品.*(多少|数量|几种|清单|列表)|多少.*(产品|商品)|有哪些(产品|商品)",
        "SELECT name AS 产品, category AS 品类, price AS 单价, stock AS 库存 FROM products "
        "ORDER BY id",
    ),
    (
        r"(消费|购买).*(最多|最高)|大客户|消费.*排行",
        "SELECT c.name AS 客户, ROUND(SUM(o.amount), 2) AS 消费总额 FROM orders o "
        "JOIN customers c ON o.customer_id = c.id WHERE o.status = '已完成' "
        "GROUP BY c.id ORDER BY 消费总额 DESC LIMIT 5",
    ),
)

# 未命中规则时展示给用户的示例问题（必须与 _RULES 自洽，测试保证）
SUPPORTED_EXAMPLES: list[str] = [
    "有多少客户？",
    "各城市的客户分布是怎样的？",
    "金牌/VIP 客户有多少？",
    "一共有多少订单？",
    "订单状态分布如何？",
    "总销售额是多少？",
    "平均订单金额（客单价）是多少？",
    "最畅销的产品是什么？",
    "各产品的销售额排行？",
    "最近 5 笔订单是什么？",
    "哪些产品库存不足需要补货？",
    "我们有哪些产品？",
    "消费最多的大客户是谁？",
]


def question_to_sql(question: str) -> str | None:
    """把自然语言问题映射为只读 SQL；未命中任何规则返回 None。"""
    for pattern, sql in _RULES:
        if re.search(pattern, question):
            return sql
    return None


# ---------- 阶段 5：双引擎抽象 ----------


@dataclass
class Translation:
    """一次翻译结果：SQL + 所用引擎（rule / llm / none）。"""

    sql: str | None
    engine: str = "none"  # "rule" | "llm" | "none"


# 引擎 code → 展示名（结果文本「引擎：规则 / LLM」）
ENGINE_LABELS: dict[str, str] = {"rule": "规则", "llm": "LLM", "none": "无"}


class Translator(Protocol):
    """翻译器协议：rule / llm / hybrid 统一入口（接口与实现分离）。"""

    async def translate(self, question: str, schema_text: str) -> Translation: ...


class RuleTranslator:
    """规则模板翻译器（离线确定性，一阶段行为）。"""

    engine = "rule"

    async def translate(self, question: str, schema_text: str) -> Translation:
        sql = question_to_sql(question)
        return Translation(sql=sql, engine=self.engine if sql else "none")


class HybridTranslator:
    """规则优先，未命中才降级 LLM（规则兜底、LLM 增强）。"""

    def __init__(self, llm: Translator) -> None:
        self._llm = llm

    async def translate(self, question: str, schema_text: str) -> Translation:
        sql = question_to_sql(question)
        if sql is not None:
            return Translation(sql=sql, engine="rule")
        return await self._llm.translate(question, schema_text)


def create_translator(mode: str = "rule", *, llm: Translator | None = None) -> Translator:
    """按模式构造翻译器。

    - rule：规则引擎（默认，行为与一阶段完全一致）
    - llm：必须提供 llm，否则 ValueError
    - hybrid：规则优先；未提供 llm 时自动退化为 rule（无 LLM 配置即降级）
    """
    if mode == "llm":
        if llm is None:
            raise ValueError("llm 模式必须提供 llm 翻译器")
        return llm
    if mode == "hybrid":
        if llm is None:
            return RuleTranslator()  # 无 LLM 配置自动退化为 rule
        return HybridTranslator(llm)
    return RuleTranslator()


async def translate(
    question: str, schema_text: str = "", *, mode: str = "rule", llm: Translator | None = None
) -> Translation:
    """模块级便捷入口：按模式翻译一次（供工具层直接调用）。"""
    return await create_translator(mode, llm=llm).translate(question, schema_text)
