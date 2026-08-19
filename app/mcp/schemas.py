"""MCP 协议层的 Pydantic 模型：网关内部统一的 Tool / ToolCall 表示。

与 `mcp.types` 解耦：上层（API 路由、Agent）只面对这里的模型，
不直接依赖 MCP SDK 的类型，方便后续扩展（如阶段 3 的 REST API）。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

# 工具名命名空间分隔符："{server}__{tool}"。
# 用双下划线而非 "."，因为 OpenAI function calling 要求工具名匹配 ^[a-zA-Z0-9_-]+$。
NAMESPACE_SEPARATOR = "__"


class ToolInfo(BaseModel):
    """一个已注册工具的网关侧描述。"""

    name: str = Field(description="全局唯一工具名（带 server 命名空间前缀）")
    original_name: str = Field(description="工具在所属 MCP Server 中的原始名称")
    server: str = Field(description="所属 MCP Server 名（config 中声明的 name）")
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict, description="JSON Schema 入参定义")


class ToolCallRequest(BaseModel):
    """一次工具调用的入参。"""

    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallResult(BaseModel):
    """一次工具调用的统一结果。"""

    content: list[dict[str, Any]] = Field(
        default_factory=list, description="MCP content 列表（text/image/... 的字典形式）"
    )
    is_error: bool = False


class ServerStatus(BaseModel):
    """单个 MCP Server 的连接状态（供阶段 3 的 /servers 接口使用）。"""

    name: str
    transport: Literal["stdio", "sse", "http", "inprocess"]
    connected: bool
    tool_count: int = 0
    error: str | None = None


class UnknownToolError(KeyError):
    """调用了 registry 中不存在的工具。"""
