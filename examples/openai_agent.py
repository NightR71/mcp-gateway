"""OpenAI function-calling Agent 示例（纯 httpx 直连，不依赖 openai SDK）。

演示「LLM Agent -> MCP Gateway -> MCP Server」完整闭环：

    用户提问 -> LLM 决定调哪个工具 -> 网关 POST /tools/{name}/call -> MCP Server 执行
        ^                                                            |
        +------------------ 工具结果回填 LLM 生成回答 -----------------+

Agent 不直连任何 MCP Server，只通过网关统一 REST API 调用聚合工具，
鉴权、限流、日志、指标全部由网关一层覆盖。

用法：
    uv run python examples/openai_agent.py "有多少客户？"           # 真实模型
    uv run python examples/openai_agent.py "有多少客户？" --mock    # 离线演示（假模型）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx

try:  # 包导入（uv run python -m examples.openai_agent / pytest）
    from .gateway_client import (
        DEFAULT_API_KEY,
        DEFAULT_BASE_URL,
        GatewayClient,
        extract_text,
        tool_to_function_schema,
    )
except ImportError:  # 脚本方式运行（uv run python examples/openai_agent.py）
    from gateway_client import (
        DEFAULT_API_KEY,
        DEFAULT_BASE_URL,
        GatewayClient,
        extract_text,
        tool_to_function_schema,
    )

# LLM 调用签名：(messages, tools) -> assistant message dict。
# message 含 tool_calls 表示模型要求调工具；只有 content 表示最终回答。
Model = Callable[[list[dict[str, Any]], list[dict[str, Any]]], Awaitable[dict[str, Any]]]

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


def build_openai_model(
    base_url: str,
    api_key: str,
    model_name: str,
    client: httpx.AsyncClient,
) -> Model:
    """构造走 OpenAI 兼容 chat/completions 的模型函数。

    OPENAI_BASE_URL 可指向任意兼容端点（One-API / Ollama / DeepSeek 等），
    这正是网关「模型无关」的体现：换模型零代码改动。
    """
    url = f"{base_url.rstrip('/')}/chat/completions"

    async def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model_name, "messages": messages, "tools": tools, "temperature": 0},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]

    return chat


class MockModel:
    """离线演示用假模型：第一轮调用一个网关工具，第二轮把结果包装成最终回答。

    不需要 OPENAI_API_KEY 即可完整演示「LLM -> 网关 -> MCP Server」闭环，
    pytest 也用它做确定性集成测试（tests/test_examples/test_openai_agent.py）。
    """

    def __init__(
        self, *, preferred_tool: str = "demo_sql__ask", call_id: str = "call_mock_1"
    ) -> None:
        self.preferred_tool = preferred_tool
        self.call_id = call_id

    def _choose_tool(self, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
        """优先选 preferred_tool，其次选名称以 __ask 结尾的工具，最后选第一个。"""
        functions = [t["function"] for t in tools]
        if not functions:
            return None
        for function in functions:
            if function["name"] == self.preferred_tool:
                return function
        for function in functions:
            if function["name"].endswith("__ask"):
                return function
        return functions[0]

    @staticmethod
    def _build_arguments(function: dict[str, Any], question: str) -> dict[str, Any]:
        """把用户提问填进工具 schema 的第一个「问题类」参数（question/message/sql/...）。"""
        properties = (function.get("parameters") or {}).get("properties") or {}
        if not properties:
            return {}
        preferred = ("question", "message", "sql", "text", "input")
        param = next((k for k in preferred if k in properties), next(iter(properties)))
        return {param: question}

    async def __call__(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        tool_results = [m for m in messages if m.get("role") == "tool"]
        if tool_results:
            content = tool_results[-1].get("content", "")
            return {
                "role": "assistant",
                "content": f"（离线演示·假模型）网关返回的工具结果：\n{content}",
            }
        function = self._choose_tool(tools)
        if function is None:
            return {
                "role": "assistant",
                "content": "（离线演示·假模型）网关没有可用工具，无法演示工具调用。",
            }
        question = str(messages[-1].get("content", ""))
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": self.call_id,
                    "type": "function",
                    "function": {
                        "name": function["name"],
                        "arguments": json.dumps(
                            self._build_arguments(function, question), ensure_ascii=False
                        ),
                    },
                }
            ],
        }


async def run_agent(
    question: str,
    *,
    model: Model,
    gateway: GatewayClient,
    functions: Sequence[dict[str, Any]] | None = None,
    max_rounds: int = 8,
) -> str:
    """通用 Agent 循环：LLM 与网关工具往返，直到模型给出最终回答。

    functions 缺省时从网关拉取并转换；显式传入空列表可跳过网关（纯问答场景）。
    """
    if functions is None:
        functions = [tool_to_function_schema(t) for t in await gateway.list_tools()]
    tools = [{"type": "function", "function": f} for f in functions]
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    for _ in range(max_rounds):
        message = await model(messages, tools)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return str(message.get("content") or "（模型未给出回答）")
        messages.append(message)
        for call in tool_calls:
            function = call["function"]
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            try:
                result = await gateway.call_tool(function["name"], arguments)
            except httpx.HTTPStatusError as exc:
                content = f"网关调用失败（HTTP {exc.response.status_code}）：{exc.response.text}"
            else:
                content = extract_text(result)
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id", ""), "content": content}
            )
    return "（达到最大往返轮数，模型仍未给出最终回答）"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="通过 MCP Gateway 调用工具的 OpenAI function-calling Agent 示例",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("question", nargs="?", default="有多少客户？", help="提问内容")
    parser.add_argument(
        "--mock", action="store_true", help="离线演示：用假模型跑完整闭环，不需要 OPENAI_API_KEY"
    )
    parser.add_argument(
        "--base-url", default=os.getenv("GATEWAY_BASE_URL", DEFAULT_BASE_URL), help="网关地址"
    )
    parser.add_argument(
        "--api-key", default=os.getenv("GATEWAY_API_KEY", DEFAULT_API_KEY), help="网关 API Key"
    )
    parser.add_argument(
        "--openai-base-url",
        default=os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
        help="OpenAI 兼容 API 地址",
    )
    parser.add_argument(
        "--openai-api-key", default=os.getenv("OPENAI_API_KEY", ""), help="OpenAI API Key"
    )
    parser.add_argument(
        "--model", default=os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL), help="模型名"
    )
    return parser


async def main() -> None:
    args = build_arg_parser().parse_args()
    if not args.mock and not args.openai_api_key:
        raise SystemExit(
            "未配置 OPENAI_API_KEY：请设置环境变量 OPENAI_API_KEY 或加 --openai-api-key；"
            "离线体验可加 --mock（假模型，不需要 Key）"
        )
    async with httpx.AsyncClient(timeout=120.0) as http_client:
        gateway = GatewayClient(args.base_url, args.api_key, client=http_client)
        try:
            tools = await gateway.list_tools()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise SystemExit(
                f"连不上网关（{gateway.base_url}）：请先在另一个终端启动网关\n"
                "    uv run uvicorn app.main:app\n"
                f"原始错误：{exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise SystemExit(
                f"网关返回错误（HTTP {exc.response.status_code}）：{exc.response.text}"
            ) from exc
        # 阶段 1：工具多时按问题语义路由，只注入 top-k，避免 context 膨胀
        total_tools = len(tools)
        if total_tools > 10:
            tools = await gateway.list_tools(query=args.question, top_k=10)
            print(f"共 {total_tools} 个工具，注入 top-K（按语义路由）：{len(tools)} 个")
        model = (
            MockModel()
            if args.mock
            else build_openai_model(
                args.openai_base_url, args.openai_api_key, args.model, http_client
            )
        )
        print(f"网关：{gateway.base_url}（已聚合 {total_tools} 个工具）")
        print(f"提问：{args.question}")
        print(
            "模型：离线假模型（--mock）"
            if args.mock
            else f"模型：{args.model}（{args.openai_base_url}）"
        )
        print("Agent 开始往返（LLM <-> 网关）...")
        functions = [tool_to_function_schema(t) for t in tools]
        answer = await run_agent(args.question, model=model, gateway=gateway, functions=functions)
        print("\n===== 最终回答 =====\n")
        print(answer)


if __name__ == "__main__":
    asyncio.run(main())
