"""API Key 鉴权：Key 的存储与查询 + 管理员 Key 的环境变量解析。

- `APIKeyStore` 是抽象接口（Protocol），当前默认实现为 SQLite；
  接口层不变即可无缝替换为 PostgreSQL / Redis，上层无感知。
- SQLite 用标准库 `sqlite3` + `asyncio.to_thread` 包装，避免阻塞事件循环；
  单连接 + asyncio.Lock 串行化访问（Key 查询频率低，无需连接池）。
- 阶段 6：`api_keys` 表含 `tenant` 与 `allowed_tools` 两列（多租户 + 工具白名单，
  allowed_tools 以 JSON 字符串保存，NULL = 不限制）；`init` 时用
  `PRAGMA table_info` 检查缺列并幂等 `ALTER TABLE` 轻量迁移，旧库免手动重建。
- M1：新增 `rate_limit_per_hour` 列（每小时配额，NULL = 不启用小时桶），走同一迁移机制。
- M6：管理员 Key **不进 Key 库**——唯一来源是环境变量 `GATEWAY_ADMIN_KEY`，
  鉴权时按请求做常量时间比较（`match_admin_key`）。这样换锁不需要迁移/清理数据，
  私密凭据也不会落进 `/tmp` 的 SQLite 或任何提交物里（配额仍可在 YAML 里配）。
"""

import asyncio
import hmac
import json
import os
import sqlite3
from pathlib import Path
from typing import Protocol

from app.config import AdminKeyPolicy, get_auth_config
from app.core.logging import get_logger
from app.schemas.auth import APIKeyInfo

logger = get_logger(__name__)

# 管理员 Key 的环境变量名（唯一来源；值绝不写进 YAML / 代码 / 文档 / 日志）
ADMIN_KEY_ENV = "GATEWAY_ADMIN_KEY"
# 管理员 Key 的建议最小长度：短串可被暴力猜解，启动时告警（不阻断启动，避免锁死自己）
ADMIN_KEY_MIN_LENGTH = 24

# 轻量迁移清单：旧库缺哪列就补哪列（列名/定义均为模块内常量，无注入风险）
_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("tenant", "TEXT NOT NULL DEFAULT 'default'"),
    ("allowed_tools", "TEXT"),
    ("rate_limit_per_hour", "INTEGER"),  # M1：每小时配额（NULL = 不启用小时桶）
    ("agent_allowed", "INTEGER"),  # M6：能否触发 Agent 模型调用（NULL = 兼容旧数据=允许）
)


class APIKeyStore(Protocol):
    """API Key 存储接口：初始化（建表 + 种子）、按 key 查询、关闭。"""

    async def init(self, seed_keys: list[APIKeyInfo]) -> None: ...

    async def get(self, key: str) -> APIKeyInfo | None: ...

    async def close(self) -> None: ...


def admin_key_from_env() -> str | None:
    """管理员 Key 原文（唯一来源：环境变量）。未设置/全空白 → None。

    每次调用现读环境变量（而不是进程启动时缓存）：本机/docker 部署下改环境变量重起
    即生效，不存在"库里还留着旧管理员 Key"这种情况——因为它压根不进库。
    """
    raw = os.getenv(ADMIN_KEY_ENV, "").strip()
    return raw or None


def build_admin_key_info(policy: AdminKeyPolicy | None = None) -> APIKeyInfo:
    """构造管理员身份（配额来自配置，凭据来自环境变量）。"""
    policy = policy if policy is not None else get_auth_config().admin
    raw = admin_key_from_env()
    return APIKeyInfo(
        key=raw or "",  # 未设置时留空串占位；只有 match_admin_key 命中才会用它
        name="admin",
        tenant=policy.tenant,
        rate_limit_per_minute=policy.rate_limit_per_minute,
        rate_limit_per_hour=policy.rate_limit_per_hour,
        allowed_tools=None,  # 管理员=运维身份，不限制工具集（它不是公开 Key）
        agent_allowed=True,  # 内部态需可自测（见 app/api/deps.py 的三态收口）
        is_admin=True,
    )


def match_admin_key(candidate: str) -> APIKeyInfo | None:
    """把候选 Key 与环境变量中的管理员 Key 做常量时间比较；命中返回管理员身份。

    用 `hmac.compare_digest` 而不是 `==`：避免比较过程提前返回泄露前缀信息
    （管理员 Key 是本系统里唯一"真随机且保密"的凭据，值得用正确姿势比）。
    未配置环境变量时恒返回 None —— 管理接口不会退化成"任意 Key 可进"。
    """
    raw = admin_key_from_env()
    if raw is None:
        return None
    if not hmac.compare_digest(candidate.encode("utf-8"), raw.encode("utf-8")):
        return None
    return build_admin_key_info()


def warn_if_admin_key_weak() -> None:
    """启动时对管理员 Key 做一次强度体检（只记长度，不记值）。"""
    raw = admin_key_from_env()
    if raw is not None and len(raw) < ADMIN_KEY_MIN_LENGTH:
        logger.warning(
            "admin_key_too_short",
            length=len(raw),
            recommended_min_length=ADMIN_KEY_MIN_LENGTH,
        )


class SQLiteAPIKeyStore:
    """基于 SQLite 的 API Key 存储（MVP 默认实现）。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：配合 asyncio.to_thread 跨线程复用同一连接
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()

    async def init(self, seed_keys: list[APIKeyInfo]) -> None:
        """建表 + 轻量迁移 + 写入种子 Key（INSERT OR IGNORE，幂等，不覆盖已有记录）。"""
        async with self._lock:
            await asyncio.to_thread(self._init_sync, seed_keys)
        logger.info("api_key_store_ready", db_path=self._db_path, seed_keys=len(seed_keys))

    def _init_sync(self, seed_keys: list[APIKeyInfo]) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                rate_limit_per_minute INTEGER NOT NULL DEFAULT 60,
                rate_limit_per_hour INTEGER,
                tenant TEXT NOT NULL DEFAULT 'default',
                allowed_tools TEXT,
                agent_allowed INTEGER
            )
            """
        )
        self._migrate_columns_sync()
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO api_keys
                (key, name, rate_limit_per_minute, rate_limit_per_hour, tenant,
                 allowed_tools, agent_allowed)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    k.key,
                    k.name,
                    k.rate_limit_per_minute,
                    k.rate_limit_per_hour,
                    k.tenant,
                    self._dump_allowed_tools(k.allowed_tools),
                    self._dump_agent_allowed(k.agent_allowed),
                )
                for k in seed_keys
            ],
        )
        self._conn.commit()

    def _migrate_columns_sync(self) -> None:
        """轻量迁移：PRAGMA table_info 检查缺列，缺则幂等 ALTER TABLE ADD COLUMN。"""
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(api_keys)")}
        for column, definition in _MIGRATION_COLUMNS:
            if column not in existing:
                self._conn.execute(f"ALTER TABLE api_keys ADD COLUMN {column} {definition}")

    @staticmethod
    def _dump_allowed_tools(allowed_tools: list[str] | None) -> str | None:
        """写转换：白名单以 JSON 字符串入库；None（不限制）存 NULL。"""
        return json.dumps(allowed_tools) if allowed_tools is not None else None

    @staticmethod
    def _parse_allowed_tools(raw: str | None) -> list[str] | None:
        """读转换：NULL → None（不限制）；JSON 损坏/非列表按 None 处理（防御脏数据）。"""
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("invalid_allowed_tools_json", raw=raw)
            return None
        return value if isinstance(value, list) else None

    @staticmethod
    def _dump_agent_allowed(agent_allowed: bool | None) -> int | None:
        """写转换（M6）：None（兼容旧行为）存 NULL，其余存 1/0。"""
        return None if agent_allowed is None else int(agent_allowed)

    @staticmethod
    def _parse_agent_allowed(raw: int | None) -> bool | None:
        """读转换（M6）：NULL → None（兼容旧数据=允许），其余按布尔取。"""
        return None if raw is None else bool(raw)

    async def get(self, key: str) -> APIKeyInfo | None:
        """按 key 查询，不存在返回 None。"""
        async with self._lock:
            row = await asyncio.to_thread(self._get_sync, key)
        if row is None:
            return None
        return APIKeyInfo(
            key=row["key"],
            name=row["name"],
            rate_limit_per_minute=row["rate_limit_per_minute"],
            rate_limit_per_hour=row["rate_limit_per_hour"],
            tenant=row["tenant"],
            allowed_tools=self._parse_allowed_tools(row["allowed_tools"]),
            agent_allowed=self._parse_agent_allowed(row["agent_allowed"]),
        )

    def _get_sync(self, key: str) -> sqlite3.Row | None:
        cursor = self._conn.execute(
            "SELECT key, name, rate_limit_per_minute, rate_limit_per_hour, tenant, "
            "allowed_tools, agent_allowed FROM api_keys WHERE key = ?",
            (key,),
        )
        return cursor.fetchone()

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)
