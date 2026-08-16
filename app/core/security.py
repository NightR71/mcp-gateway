"""API Key 鉴权：Key 的存储与查询。

- `APIKeyStore` 是抽象接口（Protocol），当前默认实现为 SQLite；
  接口层不变即可无缝替换为 PostgreSQL / Redis，上层无感知。
- SQLite 用标准库 `sqlite3` + `asyncio.to_thread` 包装，避免阻塞事件循环；
  单连接 + asyncio.Lock 串行化访问（Key 查询频率低，无需连接池）。
"""

import asyncio
import sqlite3
from pathlib import Path
from typing import Protocol

from app.core.logging import get_logger
from app.schemas.auth import APIKeyInfo

logger = get_logger(__name__)


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
        """建表并写入种子 Key（INSERT OR IGNORE，幂等，不覆盖已有记录）。"""
        async with self._lock:
            await asyncio.to_thread(self._init_sync, seed_keys)
        logger.info("api_key_store_ready", db_path=self._db_path, seed_keys=len(seed_keys))

    def _init_sync(self, seed_keys: list[APIKeyInfo]) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                rate_limit_per_minute INTEGER NOT NULL DEFAULT 60
            )
            """
        )
        self._conn.executemany(
            "INSERT OR IGNORE INTO api_keys (key, name, rate_limit_per_minute) VALUES (?, ?, ?)",
            [(k.key, k.name, k.rate_limit_per_minute) for k in seed_keys],
        )
        self._conn.commit()

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
        )

    def _get_sync(self, key: str) -> sqlite3.Row | None:
        cursor = self._conn.execute(
            "SELECT key, name, rate_limit_per_minute FROM api_keys WHERE key = ?", (key,)
        )
        return cursor.fetchone()

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)
