"""配置中心：默认值 < config/gateway.yaml < 环境变量（GATEWAY_ 前缀）。

所有配置一律从这里读取，业务代码禁止写死常量。
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from app.schemas.auth import APIKeyInfo

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_FILE = BASE_DIR / "config" / "gateway.yaml"


def _config_file() -> Path:
    return Path(os.getenv("GATEWAY_CONFIG_FILE", str(DEFAULT_CONFIG_FILE)))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """YAML 配置源：读取配置文件的 gateway 节，优先级低于环境变量。"""

    def __call__(self) -> dict[str, Any]:
        return _load_yaml(_config_file()).get("gateway", {})

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # __call__ 已返回扁平 dict，无需字段级取值
        return None, field_name, False


class Settings(BaseSettings):
    """网关运行配置。"""

    model_config = SettingsConfigDict(env_prefix="GATEWAY_")

    app_name: str = "mcp-gateway"
    version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    tool_call_timeout: float = 30.0  # 单次工具调用的读超时（秒）

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # 优先级：init 参数 > 环境变量 > YAML > 代码默认值
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
        )


class MCPServerConfig(BaseModel):
    """单个 MCP Server 的声明式配置（在 config/gateway.yaml 的 servers 节声明）。"""

    name: str
    transport: Literal["stdio", "sse", "http", "inprocess"] = "stdio"
    enabled: bool = True
    # stdio 传输
    command: str | None = None
    args: list[str] = []
    # sse / Streamable HTTP 传输
    url: str | None = None
    # inprocess 传输："package.module:attr"（attr 缺省为 server），指向 MCPServer 实例
    module: str | None = None

    @property
    def namespace(self) -> str:
        """工具名前缀，保证跨 server 工具名全局唯一。"""
        return self.name


@lru_cache
def get_settings() -> Settings:
    """获取网关配置（进程级缓存）。"""
    return Settings()


@lru_cache
def get_server_configs() -> tuple[MCPServerConfig, ...]:
    """获取 YAML 中声明的 MCP Server 列表（进程级缓存）。"""
    servers = _load_yaml(_config_file()).get("servers", [])
    return tuple(MCPServerConfig(**s) for s in servers)


class AuthConfig(BaseModel):
    """鉴权配置（config/gateway.yaml 的 auth 节）。"""

    db_path: str = "data/gateway.db"  # SQLite 路径；":memory:" 为纯内存（测试用）
    api_keys: list[APIKeyInfo] = []  # 启动时种子写入的 Key（已存在则跳过）


@lru_cache
def get_auth_config() -> AuthConfig:
    """获取鉴权配置（进程级缓存）。"""
    return AuthConfig(**_load_yaml(_config_file()).get("auth", {}))
