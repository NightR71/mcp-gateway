"""API Key 鉴权：Key 的存储与查询。

- `APIKeyStore` 是抽象接口（Protocol），当前默认实现为 SQLite；
  接口层不变即可无缝替换为 PostgreSQL / Redis，上层无感知。
- SQLite 用标准库 `sqlite3` + `asyncio.to_thread` 包装，避免阻塞事件循环；
  单连接 + asyncio.Lock 串行化访问（Key 查询频率低，无需连接池）。
- 阶段 6：`api_keys` 表含 `tenant` 与 `allowed_tools` 两列（多租户 + 工具白名单，
  allowed_tools 以 JSON 字符串保存，NULL = 不限制）；`init` 时用
  `PRAGMA table_info` 检查缺列并幂等 `ALTER TABLE` 轻量迁移，旧库免手动重建。
"""

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Protocol

from app.core.logging import get_logger
from app.schemas.auth import APIKeyInfo

logger = get_logger(__name__)

# 轻量迁移清单：旧库缺哪列就补哪列（列名/定义均为模块内常量，无注入风险）
_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("tenant", "TEXT NOT NULL DEFAULT 'default'"),
    ("allowed_tools", "TEXT"),
)


class APIKeyStore(Protocol):
    """API Key 存储接口：初始化（建表 + 种子）、按 key 查询、关闭。"""

    async def init(self, seed_keys: list[APIKeyInfo]) -> None: ...

    async def get(self, key: str) -> APIKeyInfo | None: ...

    async def close(self) -> None: ...


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
                tenant TEXT NOT NULL DEFAULT 'default',
                allowed_tools TEXT
            )
            """
        )
        self._migrate_columns_sync()
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO api_keys
                (key, name, rate_limit_per_minute, tenant, allowed_tools)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    k.key,
                    k.name,
                    k.rate_limit_per_minute,
                    k.tenant,
                    self._dump_allowed_tools(k.allowed_tools),
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
            tenant=row["tenant"],
            allowed_tools=self._parse_allowed_tools(row["allowed_tools"]),
        )

    def _get_sync(self, key: str) -> sqlite3.Row | None:
        cursor = self._conn.execute(
            "SELECT key, name, rate_limit_per_minute, tenant, allowed_tools "
            "FROM api_keys WHERE key = ?",
            (key,),
        )
        return cursor.fetchone()

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)
