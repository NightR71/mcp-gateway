"""LLM 版 NL2SQL（阶段 5）：OpenAI 兼容 chat/completions 生成只读 SQL。

- httpx 为 try-import 可选（demo server 默认零外部依赖红线，见规划文档第 13 节）；
  未装 httpx 时构造即失败，由工厂/调用方降级到规则引擎。
- **任何异常、超时、非 200、空响应一律返回 None**——规则路径绝不因 LLM 挂掉。
"""

from __future__ import annotations

import re
from typing import Any

try:
    import httpx
except ImportError:  # 未装 httpx：LLMTranslator 不可用（工厂会退回 rule）
    httpx = None  # type: ignore[assignment]

SYSTEM_PROMPT = (
    "你是数据库查询助手。根据给定的表结构，把用户的中文问题转换为一条只读 SQL。\n"
    "硬性要求：\n"
    "1. 只允许 SELECT / WITH 单语句，禁止 INSERT / UPDATE / DELETE / DDL。\n"
    "2. 只输出 SQL 本身，不要任何解释、Markdown 代码块或多余字符。\n"
    "3. 如果问题无法转换为 SQL，只输出四个字：无法生成\n"
)

# 模型输出常见噪声：Markdown 代码块包裹 / 前后空白
_SQL_CLEAN_RE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE)


class LLMTranslator:
    """OpenAI 兼容端点驱动的 NL2SQL 翻译器（模型无关，可指向 One-API/Ollama 等）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 30.0,
        client: Any | None = None,
    ) -> None:
        if httpx is None:
            raise RuntimeError(
                "未安装 httpx，LLMTranslator 不可用（demo server 需 try-import 可选）"
            )
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def question_to_sql(self, question: str, schema_text: str) -> str | None:
        """自然语言 → 只读 SQL；任何失败返回 None（规则路径兜底）。"""
        try:
            resp = await self._client.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"表结构：\n{schema_text}\n\n问题：{question}"},
                    ],
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except Exception:
            return None
        return self._clean_sql(content)

    async def translate(self, question: str, schema_text: str) -> Any:
        """Translator Protocol 入口：返回 Translation（引擎标注为 llm）。"""
        from servers.demo_sql_server.nl2sql import Translation

        sql = await self.question_to_sql(question, schema_text)
        return Translation(sql=sql, engine="llm" if sql else "none")

    @staticmethod
    def _clean_sql(content: str) -> str | None:
        text = _SQL_CLEAN_RE.sub("", (content or "").strip())
        if not text or text == "无法生成":
            return None
        return text

    async def aclose(self) -> None:
        """释放自建客户端（注入的 client 由注入方管理生命周期）。"""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
