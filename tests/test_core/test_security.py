"""API Key 存储（SQLite 实现）单元测试。"""

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
