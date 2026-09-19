"""API Key 存储（SQLite 实现）单元测试。"""

import sqlite3
from pathlib import Path

from app.core.security import SQLiteAPIKeyStore
from app.schemas.auth import APIKeyInfo

SEED_KEYS = [
    APIKeyInfo(key="k1", name="alice", rate_limit_per_minute=10),
    APIKeyInfo(key="k2", name="bob"),  # 未指定配额，默认 60
]


async def test_seed_and_get(tmp_path: Path) -> None:
    store = SQLiteAPIKeyStore(str(tmp_path / "keys.db"))
    await store.init(SEED_KEYS)
    try:
        k1 = await store.get("k1")
        assert k1 is not None
        assert k1.name == "alice"
        assert k1.rate_limit_per_minute == 10

        k2 = await store.get("k2")
        assert k2 is not None
        assert k2.rate_limit_per_minute == 60

        assert await store.get("not-exist") is None
    finally:
        await store.close()


async def test_seed_is_idempotent(tmp_path: Path) -> None:
    """重复 init 不覆盖已有 Key；换进程（新连接）重开后数据仍在。"""
    db_path = str(tmp_path / "keys.db")
    store = SQLiteAPIKeyStore(db_path)
    await store.init(SEED_KEYS)
    await store.close()

    store2 = SQLiteAPIKeyStore(db_path)
    await store2.init([APIKeyInfo(key="k1", name="hijack", rate_limit_per_minute=999)])
    try:
        k1 = await store2.get("k1")
        assert k1 is not None
        assert k1.name == "alice"
        assert k1.rate_limit_per_minute == 10
    finally:
        await store2.close()


async def test_in_memory_db() -> None:
    store = SQLiteAPIKeyStore(":memory:")
    await store.init(SEED_KEYS)
    try:
        assert await store.get("k1") is not None
        assert await store.get("nope") is None
    finally:
        await store.close()


async def test_seed_tenant_and_allowed_tools_roundtrip(tmp_path: Path) -> None:
    """tenant / allowed_tools 种子读写 round-trip；None 与空列表语义不同。"""
    store = SQLiteAPIKeyStore(str(tmp_path / "keys.db"))
    await store.init(
        [
            APIKeyInfo(
                key="t1",
                name="tenant-a",
                tenant="acme",
                allowed_tools=["demo_sql__ask", "demo_sql__run_sql"],
            ),
            APIKeyInfo(key="t2", name="all-tools", tenant="demo"),
            APIKeyInfo(key="t3", name="no-tools", tenant="demo", allowed_tools=[]),
        ]
    )
    try:
        t1 = await store.get("t1")
        assert t1 is not None
        assert t1.tenant == "acme"
        assert t1.allowed_tools == ["demo_sql__ask", "demo_sql__run_sql"]

        t2 = await store.get("t2")
        assert t2 is not None
        assert t2.tenant == "demo"
        assert t2.allowed_tools is None  # 未配置 = 不限制

        t3 = await store.get("t3")
        assert t3 is not None
        assert t3.allowed_tools == []  # 显式空白名单 ≠ 不限制
    finally:
        await store.close()


async def test_rate_limit_per_hour_roundtrip(tmp_path: Path) -> None:
    """M1 小时配额：种子写入/读回 round-trip；未配置为 None（不启用）。"""
    store = SQLiteAPIKeyStore(str(tmp_path / "keys.db"))
    await store.init(
        [
            APIKeyInfo(
                key="h1",
                name="visitor",
                rate_limit_per_minute=60,
                rate_limit_per_hour=50,
            ),
            APIKeyInfo(key="h2", name="no-hourly"),
        ]
    )
    try:
        h1 = await store.get("h1")
        assert h1 is not None
        assert h1.rate_limit_per_hour == 50

        h2 = await store.get("h2")
        assert h2 is not None
        assert h2.rate_limit_per_hour is None  # 未配置 = 不启用小时桶
    finally:
        await store.close()


async def test_migrates_legacy_db_missing_columns(tmp_path: Path) -> None:
    """旧库（缺 tenant/allowed_tools 列）init 自动补列：老数据读回默认值、重复 init 幂等。"""
    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE api_keys (
            key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            rate_limit_per_minute INTEGER NOT NULL DEFAULT 60
        )
        """
    )
    conn.execute(
        "INSERT INTO api_keys (key, name, rate_limit_per_minute) VALUES (?, ?, ?)",
        ("legacy-key", "old-user", 30),
    )
    conn.commit()
    conn.close()

    store = SQLiteAPIKeyStore(db_path)
    await store.init([])  # 空种子：仅触发建表检查 + 轻量迁移
    try:
        check = sqlite3.connect(db_path)
        try:
            columns = {row[1] for row in check.execute("PRAGMA table_info(api_keys)")}
        finally:
            check.close()
        assert {"tenant", "allowed_tools", "rate_limit_per_hour"} <= columns

        row = await store.get("legacy-key")
        assert row is not None
        assert row.tenant == "default"  # 迁移补列的默认租户
        assert row.allowed_tools is None  # 旧数据 = 不限制
        assert row.rate_limit_per_hour is None  # 旧数据 = 不启用小时桶
    finally:
        await store.close()

    # 迁移幂等：第二次 init（新连接）不报错、不重复补列、INSERT OR IGNORE 不覆盖
    store2 = SQLiteAPIKeyStore(db_path)
    await store2.init([APIKeyInfo(key="legacy-key", name="hijack", rate_limit_per_minute=999)])
    try:
        row = await store2.get("legacy-key")
        assert row is not None
        assert row.name == "old-user"
        assert row.rate_limit_per_minute == 30
        assert row.tenant == "default"
        assert row.allowed_tools is None
    finally:
        await store2.close()
