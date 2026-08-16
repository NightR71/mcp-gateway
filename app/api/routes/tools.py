"""统一工具 API：GET /tools 列工具，POST /tools/{tool_name}/call 调用。"""

from fastapi import APIRouter, HTTPException

from app.api.deps import ProtectedDep, RegistryDep
from app.core.logging import get_logger
from app.mcp.schemas import ToolCallRequest, ToolCallResult, ToolInfo, UnknownToolError

router = APIRouter(tags=["tools"])
logger = get_logger(__name__)


@router.get("/tools", response_model=list[ToolInfo])
async def list_tools(registry: RegistryDep, _key: ProtectedDep) -> list[ToolInfo]:
    """列出网关聚合的全部工具（带 server 命名空间前缀）。"""
    return registry.list_tools()


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
