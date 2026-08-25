"""MCP Server 状态查询 API。"""

from fastapi import APIRouter

from app.api.deps import ProtectedDep, RegistryDep
from app.mcp.schemas import ServerStatus

router = APIRouter(tags=["servers"])


@router.get("/servers", response_model=list[ServerStatus])
async def list_servers(registry: RegistryDep, api_key: ProtectedDep) -> list[ServerStatus]:
    """列出所有声明 server 的连接状态（含连接失败的）。

    阶段 6：tool_count 按当前 Key 可见的工具数统计（白名单）。
    """
    return registry.server_status(api_key)
