"""LangChain Agent 示例：把网关聚合的工具包装成 LangChain Tools 绑定给模型。

与 openai_agent.py 同一条闭环，只是 Agent 框架换成 LangChain（langchain-core +
langchain-openai）。工具执行仍直接走网关统一入口，不经过 LangChain 的 ToolNode——
这正是网关的意义：鉴权、限流、日志、指标全部收敛在网关一层。

依赖（可选组，网关本体不依赖）：
    uv sync --group agent

用法：
    uv run python examples/langchain_agent.py "有多少客户？"
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

try:  # 包导入（uv run python -m examples.langchain_agent / pytest）
    from .gateway_client import DEFAULT_API_KEY, DEFAULT_BASE_URL, GatewayClient, extract_text
except ImportError:  # 脚本方式运行（uv run python examples/langchain_agent.py）
    from gateway_client import DEFAULT_API_KEY, DEFAULT_BASE_URL, GatewayClient, extract_text

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


def tool_to_langchain_tool(tool: dict[str, Any], gateway: GatewayClient) -> StructuredTool:
    """把网关 ToolInfo 包装成 LangChain 异步 Tool（执行时仍走网关统一入口）。

    args_schema 由网关下发的 JSON Schema 动态生成；参数一律放宽为 Any，
    演示场景够用，生产可按 JSON Schema 映射精确的 pydantic 类型。
    """
    name: str = tool["name"]
    properties = (tool.get("input_schema") or {}).get("properties") or {}
    fields = {
        prop: (Any, Field(default=None, description=str(spec.get("description") or "")))
        for prop, spec in properties.items()
    }
    args_schema = create_model(f"{name}_args", **fields)

    async def call_through_gateway(**arguments: Any) -> str:
        # 模型未传的参数落为 None，过滤后交给网关（网关侧 schema 仍会兜底校验）
        payload = {k: v for k, v in arguments.items() if v is not None}
        result = await gateway.call_tool(name, payload)
        return extract_text(result)

    call_through_gateway.__name__ = f"call_{name.replace('__', '_')}"
    return StructuredTool.from_function(
        coroutine=call_through_gateway,
        name=name,
        description=tool.get("description") or name,
        args_schema=args_schema,
    )


async def run_agent(
    question: str,
    *,
    model: BaseChatModel,
    gateway: GatewayClient,
    max_rounds: int = 8,
) -> str:
    """LangChain 版 Agent 循环：模型（已 bind_tools）与网关工具往返。"""
    messages: list[BaseMessage] = [HumanMessage(content=question)]
    for _ in range(max_rounds):
        response = await model.ainvoke(messages)
        messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return str(response.content)
        for call in tool_calls:
            try:
                result = await gateway.call_tool(call["name"], call.get("args") or {})
                content = extract_text(result)
            except httpx.HTTPStatusError as exc:
                content = f"网关调用失败（HTTP {exc.response.status_code}）：{exc.response.text}"
            messages.append(ToolMessage(content=content, tool_call_id=call["id"]))
    return "（达到最大往返轮数，模型仍未给出最终回答）"


def build_llm(model_name: str, api_key: str, base_url: str | None = None) -> BaseChatModel:
    """构造 ChatOpenAI；缺依赖时给出可操作的提示（langchain-openai 仅示例需要）。"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError(
            "缺少 langchain-openai：请先执行 `uv sync --group agent`（仅示例需要，网关本体不依赖）"
        ) from exc
    kwargs: dict[str, Any] = {"model": model_name, "api_key": api_key, "temperature": 0}
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="通过 MCP Gateway 调用工具的 LangChain Agent 示例",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("question", nargs="?", default="有多少客户？", help="提问内容")
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
    if not args.openai_api_key:
        raise SystemExit(
            "未配置 OPENAI_API_KEY：请设置环境变量 OPENAI_API_KEY 或加 --openai-api-key"
        )
    async with httpx.AsyncClient(timeout=120.0) as http_client:
        gateway = GatewayClient(args.base_url, args.api_key, client=http_client)
        tools_info = await gateway.list_tools()
        tools = [tool_to_langchain_tool(t, gateway) for t in tools_info]
        model = build_llm(args.model, args.openai_api_key, args.openai_base_url).bind_tools(tools)
        print(f"网关：{gateway.base_url}（已聚合 {len(tools_info)} 个工具）")
        print(f"提问：{args.question}")
        print(f"模型：{args.model}（{args.openai_base_url}）")
        print(f"已包装 {len(tools)} 个 LangChain Tool，Agent 开始往返（LLM <-> 网关）...")
        answer = await run_agent(args.question, model=model, gateway=gateway)
        print("\n===== 最终回答 =====\n")
        print(answer)


if __name__ == "__main__":
    asyncio.run(main())
