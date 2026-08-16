"""MCP Server 状态查询 API。"""

from fastapi import APIRouter

from app.api.deps import ProtectedDep, RegistryDep
from app.mcp.schemas import ServerStatus

router = APIRouter(tags=["servers"])


@router.get("/servers", response_model=list[ServerStatus])
async def list_servers(registry: RegistryDep, _key: ProtectedDep) -> list[ServerStatus]:
    """列出所有声明 server 的连接状态（含连接失败的）。"""
    return registry.server_status()
