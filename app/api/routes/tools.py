"""统一工具 API：GET /tools（可选 query 语义路由）+ POST /tools/{name}/call（同步/SSE）。"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api.deps import ProtectedDep, RegistryDep, RouterDep, router_enabled
from app.core.logging import get_logger
from app.mcp.schemas import ToolCallRequest, ToolCallResult, ToolInfo, UnknownToolError

router = APIRouter(tags=["tools"])
logger = get_logger(__name__)


@router.get("/tools", response_model=list[ToolInfo])
async def list_tools(
    registry: RegistryDep,
    _key: ProtectedDep,
    router: RouterDep,
    query: str | None = None,
    top_k: int | None = None,
) -> list[ToolInfo]:
    """列出网关聚合的全部工具（带 server 命名空间前缀）。

    - 无 `query` 时行为与一阶段完全一致（全量返回）。
    - 有 `query` 且路由启用（routing.enabled）时按关键词语义过滤并 top-k 截断，
      供 Agent 只注入相关工具（阶段 1 Semantic Tool Routing）。
    """
    tools = registry.list_tools()
    if query and router_enabled():
        return router.search(query, tools, top_k)
    return tools


@router.post("/tools/{tool_name}/call", response_model=ToolCallResult)
async def call_tool(
    tool_name: str, body: ToolCallRequest, registry: RegistryDep, api_key: ProtectedDep
) -> ToolCallResult:
    """按命名空间工具名路由到对应 MCP Server 调用。"""
    try:
        return await registry.call_tool(tool_name, body.arguments)
    except UnknownToolError:
        raise HTTPException(status_code=404, detail=f"未知工具: {tool_name}") from None
    except Exception as exc:
        logger.error("tool_call_failed", tool=tool_name, caller=api_key.name, error=str(exc))
        raise HTTPException(status_code=502, detail=f"工具调用失败: {exc}") from exc


@router.post("/tools/{tool_name}/call/stream")
async def call_tool_stream(
    tool_name: str,
    body: ToolCallRequest,
    registry: RegistryDep,
    api_key: ProtectedDep,
) -> StreamingResponse:
    """SSE 流式工具调用（阶段 4）：事件顺序 `start` → `result` → `done`。

    内部仍调用 registry.call_tool()；同步 `/tools/{tool_name}/call` 一字不改。
    未知工具在流开始前校验（保持 404 语义）；调用失败发 `error` 事件。
    """
    try:
        registry.get_tool(tool_name)  # 流开始前校验，保证 404 语义不变
    except UnknownToolError:
        raise HTTPException(status_code=404, detail=f"未知工具: {tool_name}") from None

    async def event_source() -> AsyncIterator[str]:
        yield f"event: start\ndata: {json.dumps({'tool': tool_name}, ensure_ascii=False)}\n\n"
        try:
            result = await registry.call_tool(tool_name, body.arguments)
            yield f"event: result\ndata: {result.model_dump_json()}\n\n"
        except Exception as exc:
            logger.error("tool_call_stream_failed", tool=tool_name, error=str(exc))
            yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")
