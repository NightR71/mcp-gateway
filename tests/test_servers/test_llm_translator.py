"""阶段 5：LLMTranslator 单元测试（httpx MockTransport，零真实网络）。

覆盖：命中返回 SQL / prompt 含表结构 / 异常与非 200 返回 None / 缺 httpx 降级。
"""

import httpx
import pytest

import servers.demo_sql_server.llm_translator as llm_translator
from servers.demo_sql_server.llm_translator import LLMTranslator

SCHEMA = (
    "CREATE TABLE customers (id INTEGER, name TEXT); CREATE TABLE orders (id INTEGER, amount REAL);"
)
QUESTION = "各城市客户分布？"
SQL_ANSWER = "SELECT city, COUNT(*) FROM customers GROUP BY city"


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_question_to_sql_hit() -> None:
    """命中：正常响应返回清洗后的 SQL（去掉 Markdown 代码块）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": f"```sql\n{SQL_ANSWER}\n```"}}]}
        )

    translator = LLMTranslator("http://fake/v1", "key", "gpt-test", client=_mock_client(handler))
    sql = await translator.question_to_sql(QUESTION, SCHEMA)
    assert sql == SQL_ANSWER
    await translator.aclose()


async def test_prompt_contains_schema_and_question() -> None:
    """prompt 含表结构关键词：请求体携带表结构与问题。"""
    import json as _json

    captured: dict = {}

    async def capture(request: httpx.Request) -> httpx.Response:
        captured["payload"] = request
        return httpx.Response(200, json={"choices": [{"message": {"content": SQL_ANSWER}}]})

    translator = LLMTranslator("http://fake/v1", "key", "gpt-test", client=_mock_client(capture))
    await translator.question_to_sql(QUESTION, SCHEMA)
    payload = captured["payload"]
    assert payload.url.path == "/v1/chat/completions"
    body = _json.loads(payload.read().decode("utf-8"))
    system = body["messages"][0]["content"]
    user = body["messages"][1]["content"]
    assert "SELECT" in system and "表结构" in system  # 只读约束 + 表结构说明
    assert "customers" in user and QUESTION in user
    await translator.aclose()


async def test_non_200_returns_none() -> None:
    """非 200 响应返回 None（规则路径绝不因 LLM 挂掉）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    translator = LLMTranslator("http://fake/v1", "key", "gpt-test", client=_mock_client(handler))
    assert await translator.question_to_sql(QUESTION, SCHEMA) is None
    await translator.aclose()


async def test_network_error_returns_none() -> None:
    """网络异常 / 超时返回 None。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    translator = LLMTranslator("http://fake/v1", "key", "gpt-test", client=_mock_client(handler))
    assert await translator.question_to_sql(QUESTION, SCHEMA) is None
    await translator.aclose()


async def test_empty_and_cannot_generate_returns_none() -> None:
    """空响应与「无法生成」返回 None。"""

    def handler(content: str):
        def _h(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        return _h

    for content in ("", "   ", "无法生成"):
        translator = LLMTranslator(
            "http://fake/v1", "key", "gpt-test", client=_mock_client(handler(content))
        )
        assert await translator.question_to_sql(QUESTION, SCHEMA) is None
        await translator.aclose()


def test_constructor_degrades_without_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    """未装 httpx 时构造失败（工厂/调用方降级规则引擎）。"""
    monkeypatch.setattr(llm_translator, "httpx", None)
    with pytest.raises(RuntimeError, match="httpx"):
        LLMTranslator("http://fake/v1", "key", "gpt-test")
