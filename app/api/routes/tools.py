"""统一工具 API：GET /tools 列工具（可选 query 语义路由），POST /tools/{tool_name}/call 调用。"""

from fastapi import APIRouter, HTTPException

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
