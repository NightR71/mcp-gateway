"""网关统一 REST API 的极简异步客户端（Agent 示例专用，不依赖网关内部代码）。

上层 Agent 只需要知道两个 HTTP 契约（与 app/api/routes/tools.py 对应）：

    GET  /tools                 -> [ToolInfo]         列出全部聚合工具
    POST /tools/{name}/call     -> ToolCallResult     按命名空间工具名调用

所有请求带 `X-API-Key` 走网关鉴权；超限时网关返回 429 并带 Retry-After
（限流策略见 app/core/rate_limit.py）。本示例聚焦「Agent → 网关 → MCP Server」
闭环本身，不做重试/退避，生产可在此基础上加 tenacity。
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"
# 演示 Key（config/gateway.yaml 的 auth 节启动时种子写入），生产环境必须换成自己的 Key
DEFAULT_API_KEY = "dev-key-please-change"


def tool_to_function_schema(tool: dict[str, Any]) -> dict[str, Any]:
    """把网关 ToolInfo 转成 OpenAI function calling 的 function 描述。

    网关工具名统一带 `{server}__{tool}` 命名空间前缀（如 demo_sql__ask），
    天然满足 OpenAI / LangChain 工具命名规则 `^[a-zA-Z0-9_-]+$`
    （见 app/mcp/schemas.py 中 NAMESPACE_SEPARATOR 的设计说明）。
    """
    input_schema = dict(tool.get("input_schema") or {})
    input_schema.setdefault("type", "object")
    return {
        "name": tool["name"],
        "description": tool.get("description") or tool["name"],
        "parameters": input_schema,
    }


def extract_text(result: dict[str, Any]) -> str:
    """从 ToolCallResult 的 content 列表提取纯文本（MCP text content 块）。"""
    parts: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(str(item))
    text = "\n".join(part.strip() for part in parts if part.strip())
    return text or "（工具无文本输出）"


class GatewayClient:
    """网关 HTTP 客户端；client 参数可注入 httpx.AsyncClient（测试用 ASGI 传输）。"""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = DEFAULT_API_KEY,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def __aenter__(self) -> GatewayClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """关闭内部客户端；注入的客户端由注入方管理生命周期。"""
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}

    async def list_tools(
        self, query: str | None = None, top_k: int | None = None
    ) -> list[dict[str, Any]]:
        """列出网关聚合的工具（ToolInfo 列表的字典形式）。

        query 缺省时全量返回（与一阶段行为一致）；传 query 时网关按关键词
        语义过滤（阶段 1 Semantic Tool Routing），top_k 控制返回数量上限。
        """
        params: dict[str, Any] = {}
        if query is not None:
            params["query"] = query
        if top_k is not None:
            params["top_k"] = top_k
        resp = await self._client.get(
            f"{self.base_url}/tools", headers=self._headers(), params=params
        )
        resp.raise_for_status()
        return resp.json()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """按命名空间工具名调用工具，返回 ToolCallResult 的字典形式。"""
        resp = await self._client.post(
            f"{self.base_url}/tools/{tool_name}/call",
            headers=self._headers(),
            json={"arguments": arguments},
        )
        resp.raise_for_status()
        return resp.json()
