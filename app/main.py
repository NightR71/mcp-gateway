"""FastAPI 应用入口：挂路由、横切层、启动/清理事件。"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.agent.models import MockModel, build_openai_model
from app.agent.persona import ResumePersona
from app.agent.runner import AgentRunner
from app.api.routes import admin as admin_route
from app.api.routes import agent, health, servers, tools
from app.api.routes import metrics as metrics_route
from app.config import (
    get_agent_config,
    get_auth_config,
    get_resume_kb_config,
    get_router_config,
    get_server_configs,
    get_settings,
)
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.logging import configure_logging, get_logger
from app.core.metrics import setup_metrics
from app.core.mode import build_mode_store
from app.core.output_guard import OutputGuard
from app.core.rate_limit import RateLimiter
from app.core.security import SQLiteAPIKeyStore, admin_key_from_env, warn_if_admin_key_weak
from app.mcp.registry import ToolRegistry
from app.mcp.tool_router import ToolRouter

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("gateway_started", app_name=settings.app_name, version=settings.version)

    # 协议层：连接所有 MCP Server 并聚合工具
    registry = ToolRegistry(get_server_configs(), tool_call_timeout=settings.tool_call_timeout)
    await registry.connect_all()
    app.state.registry = registry

    # 语义工具路由（阶段 1：GET /tools?query= 关键词过滤 + top-k）
    router_config = get_router_config()
    app.state.router = ToolRouter(top_k=router_config.top_k, min_tools=router_config.min_tools)

    # Agent 能力（阶段 2）：按配置惰性创建，未启用保持 None（路由返回 503）
    agent_runner = _build_agent_runner(registry, app.state.router)
    app.state.agent_runner = agent_runner

    # 横切层：API Key 存储（建表 + 种子）与令牌桶限流器
    auth_config = get_auth_config()
    key_store = SQLiteAPIKeyStore(auth_config.db_path)
    await key_store.init(auth_config.api_keys)
    app.state.key_store = key_store
    app.state.rate_limiter = RateLimiter()

    # M6 三态公开开关：默认只读（改配置 + 重新部署才生效），单进程部署可开 process 写入。
    # 管理员 Key 不在这里播种——它只存在于环境变量，鉴权时按请求现算（app/core/security.py）。
    # 注意这里**不传模块级 settings**：模块级对象是 import 时求值的，测试会换配置文件，
    # 传它会让开关读到与本次启动无关的旧配置（get_settings() 走缓存，进程内一致）。
    app.state.mode_store = build_mode_store()
    warn_if_admin_key_weak()
    logger.info(
        "public_mode_ready",
        public_mode=app.state.mode_store.get(),
        source=app.state.mode_store.source,
        writable=app.state.mode_store.writable,
        admin_key_configured=admin_key_from_env() is not None,
    )

    yield

    if agent_runner is not None:
        await agent_runner.close()
    await registry.close()
    await key_store.close()
    logger.info("gateway_stopped")


def _build_agent_runner(registry: ToolRegistry, router: ToolRouter) -> AgentRunner | None:
    """按 AgentConfig 构造 AgentRunner；未启用/未配 Key 且非 mock 时返回 None。

    M3：同一构造点装配简历 Agent 的人设（系统提示词 + 固定回应）与输出守门；
    未配置 `resume_kb` 节时二者为 None，行为与旧版完全一致。
    """
    config = get_agent_config()
    if not config.enabled:
        logger.info("agent_disabled")
        return None

    extras = _build_resume_extras()
    if config.mock:
        logger.info("agent_ready", mode="mock")
        return AgentRunner(
            registry,
            MockModel(),
            router=router,
            max_rounds=config.max_rounds,
            routing_top_k=config.routing_top_k,
            **extras,
        )
    api_key = os.getenv("GATEWAY_AGENT_API_KEY", "")
    if not api_key:
        logger.warning("agent_no_api_key")
        return None
    model = build_openai_model(
        config.base_url,
        api_key,
        config.model,
        connect_retries=config.model_connect_retries,
        max_tokens=config.max_tokens,
    )
    return AgentRunner(
        registry,
        model,
        router=router,
        max_rounds=config.max_rounds,
        routing_top_k=config.routing_top_k,
        **extras,
    )


def _build_resume_extras() -> dict[str, Any]:
    """构造简历 Agent 的可选增强（人设 / 输出守门），未配置时为 None。

    配置全部来自 `config/gateway.yaml` 的 `resume_kb` 节——换人设、改红线、
    调打码模式都只改 YAML，不动代码。
    """
    kb_config = get_resume_kb_config()
    persona: ResumePersona | None = None
    guard: OutputGuard | None = None
    if kb_config.persona.enabled:
        persona = ResumePersona.from_config(kb_config)
        logger.info(
            "resume_persona_ready",
            name=kb_config.persona.name,
            policies=len(kb_config.persona.reply_policies),
            rules=len(kb_config.persona.rules),
        )
    if kb_config.output_guard.enabled:
        guard = OutputGuard(kb_config.output_guard)
        logger.info("resume_output_guard_ready", patterns=guard.rule_names)
    return {"persona": persona, "output_guard": guard}


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
    setup_metrics(app)
    app.include_router(health.router)
    app.include_router(tools.router)
    app.include_router(servers.router)
    app.include_router(agent.router)
    # M1 §5.3：/metrics 按配置暴露（默认 404；开启后默认要求 API Key）
    app.include_router(metrics_route.router)
    # M6 决策 8：三态公开开关管理接口（管理员 Key 保护，见 app/api/routes/admin.py）
    app.include_router(admin_route.router)
    # M1 §5.3 入参设界：请求体大小上限（413），上限值每请求读自 Settings
    app.add_middleware(BodySizeLimitMiddleware)

    # 阶段 3：Interactive Agent Workbench（纯静态三件套，随仓库提交，Vercel 一体化部署）
    ui_dir = Path(__file__).resolve().parent / "ui"
    # M4：简历助手前端（/chat 交互对话 + /resume 脱敏简历页），同为纯静态、零构建链
    chat_dir = Path(__file__).resolve().parent / "chat"
    resume_dir = Path(__file__).resolve().parent / "resume"

    @app.get("/ui", include_in_schema=False)
    async def ui_index() -> FileResponse:
        """/ui 精确路径直接返回工作台首页（StaticFiles 对目录路径会 307 到 /ui/）。"""
        return FileResponse(ui_dir / "index.html")

    app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")

    @app.get("/chat", include_in_schema=False)
    async def chat_index() -> FileResponse:
        """/chat 简历助手对话页（M4）。"""
        return FileResponse(chat_dir / "index.html")

    app.mount("/chat", StaticFiles(directory=chat_dir, html=True), name="chat")

    @app.get("/resume", include_in_schema=False)
    async def resume_index() -> FileResponse:
        """/resume 脱敏综合简历页（M4）；AI 话术与界面常驻入口都指向它。"""
        return FileResponse(resume_dir / "index.html")

    app.mount("/resume", StaticFiles(directory=resume_dir, html=True), name="resume")

    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        """/ 指向 /chat（M6 决策：业务应用为主入口，不再把网关工作台作为默认落地页）。

        历史：M4 之前 / 指向 /ui（基础设施侧演示工作台）。M6 的诉求是"不再对外展示网关
        入口、以简历助手为主入口"，故根路径改为 /chat；/ui 页面与功能一字未改，仍可直达。
        """
        return RedirectResponse(url="/chat")

    return app


app = create_app()
