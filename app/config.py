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

from app.schemas.admin import PublicMode
from app.schemas.auth import APIKeyInfo

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_FILE = BASE_DIR / "config" / "gateway.yaml"
# 简历知识库应用层配置（人设/拒答/守门）：与部署平台无关，单独一份，三套平台配置不重复
DEFAULT_RESUME_KB_FILE = BASE_DIR / "config" / "resume_kb.yaml"


def _config_file() -> Path:
    return Path(os.getenv("GATEWAY_CONFIG_FILE", str(DEFAULT_CONFIG_FILE)))


def _resume_kb_file() -> Path:
    return Path(os.getenv("RESUME_KB_CONFIG_FILE", str(DEFAULT_RESUME_KB_FILE)))


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
    max_request_body_bytes: int = 1_000_000  # 请求体大小上限（字节，M1 入参设界）
    # M6 三态公开开关（决策 8）：public / internal / closed。
    # 默认 closed（fail-closed，与 /metrics 默认关闭、allow_mock_demo 默认 false 同惯例）：
    # 配置缺失时对外不可用，而不是"静默地继续对外可用"。线上由平台配置显式开 public。
    public_mode: PublicMode = "closed"
    # 开关的存储形态：
    # - config（默认）：只读；值来自配置，改值必须改配置并重新部署
    #   （多实例 Serverless 的确定性路径）；
    # - process：进程内可写（单进程部署：本机 / docker / 单实例 VPS），管理接口可即时切换。
    # 详见 app/core/mode.py 的确定性边界说明。
    public_mode_store: Literal["config", "process"] = "config"

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


def public_mode_source() -> str:
    """三态开关当前生效值的来源：env / yaml / default。

    为什么要显式算来源：`Settings` 的优先级是「环境变量 > YAML > 默认值」，
    运维切换时最容易踩的坑是"我以为它读的是环境变量，其实是 YAML"（或反过来）。
    管理接口把来源如实上报，判断"切了没生效"就不用猜（见 docs/M6-部署操作手册.md §3.4）。
    """
    if any(name.upper() == "GATEWAY_PUBLIC_MODE" for name in os.environ):
        return "env"
    if "public_mode" in (_load_yaml(_config_file()).get("gateway", {}) or {}):
        return "yaml"
    return "default"


@lru_cache
def get_server_configs() -> tuple[MCPServerConfig, ...]:
    """获取 YAML 中声明的 MCP Server 列表（进程级缓存）。"""
    servers = _load_yaml(_config_file()).get("servers", [])
    return tuple(MCPServerConfig(**s) for s in servers)


class AdminKeyPolicy(BaseModel):
    """管理员 Key 的**非凭据**策略（config/gateway.yaml 的 auth.admin 节，M6）。

    **这里只有配额与租户，永远没有 Key 值**：管理员 Key 的唯一来源是环境变量
    `GATEWAY_ADMIN_KEY`（见 app/core/security.py），不进 YAML、不进仓库、不落库。
    放在配置里的理由：配额属于"可调策略"（禁止在代码里写死常量），而凭据不是配置。
    """

    tenant: str = "admin"
    rate_limit_per_minute: int = 30
    rate_limit_per_hour: int | None = 300


class AuthConfig(BaseModel):
    """鉴权配置（config/gateway.yaml 的 auth 节）。"""

    db_path: str = "data/gateway.db"  # SQLite 路径；":memory:" 为纯内存（测试用）
    api_keys: list[APIKeyInfo] = []  # 启动时种子写入的 Key（已存在则跳过）
    admin: AdminKeyPolicy = AdminKeyPolicy()  # 管理员 Key 的配额（凭据只走环境变量）


@lru_cache
def get_auth_config() -> AuthConfig:
    """获取鉴权配置（进程级缓存）。"""
    return AuthConfig(**_load_yaml(_config_file()).get("auth", {}))


class ToolRouterConfig(BaseModel):
    """语义工具路由配置（config/gateway.yaml 的 routing 节，阶段 1）。"""

    enabled: bool = True
    top_k: int = 10  # 命中后返回的候选工具数上限
    min_tools: int = 10  # 工具总数不超过该值时不做过滤（小工具集零过滤）


@lru_cache
def get_router_config() -> ToolRouterConfig:
    """获取工具路由配置（进程级缓存），缺省用默认值。"""
    return ToolRouterConfig(**_load_yaml(_config_file()).get("routing", {}))


class AgentConfig(BaseModel):
    """网关内 Agent 能力配置（config/gateway.yaml 的 agent 节，阶段 2）。

    LLM API Key **只从环境变量 GATEWAY_AGENT_API_KEY 读取**，绝不进 YAML。
    """

    enabled: bool = False
    mock: bool = False  # 离线演示：用假模型，不需要 LLM Key
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    max_rounds: int = 8
    routing_top_k: int = 10
    # 传输层连接重试次数（M5 真模型冒烟实测的必需项，默认 10）。
    #
    # 背景：M5 冒烟时 ~93% 的提问在第 1~3 轮的模型调用上失败。实测根因是
    # api.deepseek.com 在本机网络下解析到 3 个 IP，其中部分 IP 的 TLS 证书链
    # 被 Python 校验拒绝（self-signed certificate in certificate chain）——
    # **新建连接约 60% 失败**（20 次采样 12 次失败），而 Agent 一问要 2~3 次模型调用。
    # httpx 的 AsyncHTTPTransport(retries=N) 会在连接错误上重连（会重新解析 DNS）。
    #
    # 为什么是 10：按「单次尝试失败率 0.6」估算，N 次重试（共 N+1 次尝试）后单次
    # 模型调用的失败率为 0.6^(N+1)。实测校验：
    #   - retries=3  → 0/20 失败（同一实验）；
    #   - retries=5  → 两轮 31 问冒烟里各失败 2 与 3 问（约 8%，即 0.6^6≈4.7% × 每问 2~3 次调用）；
    #   - retries=10 → 单次调用失败率降到 0.6^11≈0.4%，一问约 1%，故取 10 作为默认。
    # 连接失败不消耗 token、每次尝试仅多花一次 TCP+TLS 握手，代价可忽略。
    #
    # 注意：重试**不降低任何安全校验**——每次尝试仍然完整校验证书，只是换一条连接；
    # 若真被中间人劫持，所有尝试都会失败并如实报错。也因此这里只重试连接层错误
    # （httpx 的 retries 只覆盖 ConnectError/ConnectTimeout），**不会重试 4xx（含 429）**；
    # 提供方返回 5xx 也不在其覆盖内（那是另一类问题，需另做退避策略）。
    model_connect_retries: int = 10


@lru_cache
def get_agent_config() -> AgentConfig:
    """获取 Agent 配置（进程级缓存），缺省用默认值。"""
    return AgentConfig(**_load_yaml(_config_file()).get("agent", {}))


class MetricsConfig(BaseModel):
    """/metrics 暴露策略（config/gateway.yaml 的 metrics 节，M1 §5.3）。

    - enabled=false（默认）：不暴露任何指标，/metrics 返回 404（公网默认关闭）；
    - enabled=true + require_auth=true（默认）：需有效 API Key（401/429 语义同其他接口）；
    - enabled=true + require_auth=false：匿名可读（仅限本机/内网部署自行选择）。
    """

    enabled: bool = False
    require_auth: bool = True


@lru_cache
def get_metrics_config() -> MetricsConfig:
    """获取 /metrics 暴露配置（进程级缓存），缺省用默认值。"""
    return MetricsConfig(**_load_yaml(_config_file()).get("metrics", {}))


class ReplyPolicyConfig(BaseModel):
    """一条输入侧固定回应策略（config/gateway.yaml 的 resume_kb.reply_policies）。

    - `trigger_patterns`：命中任一即视为该话题域（子串匹配，大小写不敏感）；
    - `scope_hints`：非空时要求**同时**命中其中一个，才触发本策略
      （用于把红线限制在特定话题域，如「性能优化」只在海洋数据语境下拦截）；
    - `reply_key`：命中后返回 `fallbacks` 中该键的固定文案（不走模型）。
    """

    id: str
    trigger_patterns: list[str] = []
    scope_hints: list[str] = []
    reply_key: str = "out_of_scope"


class PersonaConfig(BaseModel):
    """简历 Agent 人设与拒答配置（config/gateway.yaml 的 resume_kb.persona）。

    全部文案与规则来自配置，代码只做拼接与匹配——换人设/改红线不需要改代码。
    """

    enabled: bool = False
    name: str = "技术代言人"
    intro: str = ""  # 角色定位描述（系统提示词首段）
    rules: list[str] = []  # 行为规则（逐条进系统提示词）
    redlines: list[str] = []  # 红线禁令（逐条进系统提示词）
    fallbacks: dict[str, str] = {}  # reply_key -> 固定话术
    reply_policies: list[ReplyPolicyConfig] = []


class PIIPatternConfig(BaseModel):
    """一条 PII 打码模式（config/gateway.yaml 的 resume_kb.output_guard.patterns）。

    打码为**等长替换**：保留前 `keep_prefix` / 后 `keep_suffix` 个字符，
    中间全部替换为 `mask_char`——长度不变，流式渲染不跳动。
    """

    name: str
    pattern: str
    keep_prefix: int = 0
    keep_suffix: int = 0
    mask_char: str = "*"


class OutputGuardConfig(BaseModel):
    """输出守门配置（config/gateway.yaml 的 resume_kb.output_guard）。"""

    enabled: bool = False
    window_chars: int = 64  # 流式滑窗大小：hold 尾部 N 字符，覆盖跨 token 的 PII
    patterns: list[PIIPatternConfig] = []


class FrontendConfig(BaseModel):
    """`/chat` 前端行为配置（config/resume_kb.yaml 的 frontend 节，M4）。

    - `allow_mock_demo`：mock 模式下是否允许前端发起对话。**默认 false**——
      对外访问时 mock 剧本会冒充真实回答（决策 6 禁止），前端改显示维护页；
      仅本地开发/验收时置 true 用于走通链路（此时回答带「离线演示」标识）。
    - `maintenance_hint`：维护页展示的固定文案（配置化，不写死）。
    - `preset_questions`：右栏气泡卡的预设问题（按引导顺序排列，点击即送进对话流）。
    """

    allow_mock_demo: bool = False
    maintenance_hint: str = "AI 答疑正在准备中。可先查看右侧简历入口与项目卡片，或稍后再来。"
    preset_questions: list[str] = []


class ResumeKBConfig(BaseModel):
    """简历知识库应用层配置（config/resume_kb.yaml，M3/M4）。

    独立文件而非平台配置的一节：人设/红线/守门/前端行为都是**业务应用层**配置，
    与部署平台无关（平台配置只管 server 传输方式与 Key 配额），单独一份避免副本漂移。
    路径可用环境变量 `RESUME_KB_CONFIG_FILE` 覆盖。

    与 servers/resume_kb_server 的关系：本配置作用于 **Agent 层与前端**（提示词、
    拒答、输出打码、气泡卡），Server 侧只负责只读检索。
    """

    server_name: str = "resume_kb"
    persona: PersonaConfig = PersonaConfig()
    output_guard: OutputGuardConfig = OutputGuardConfig()
    frontend: FrontendConfig = FrontendConfig()


@lru_cache
def get_resume_kb_config() -> ResumeKBConfig:
    """获取简历知识库配置（进程级缓存），缺省用默认值（功能关闭）。"""
    return ResumeKBConfig(**_load_yaml(_resume_kb_file()))
