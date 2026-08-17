"""演示业务库单元测试：初始化幂等、只读校验、查询执行。

注意用临时文件而非 ":memory:"——每次 sqlite3.connect(":memory:")
都是独立内存库，跨连接看不到对方建的表。
"""

from pathlib import Path

import pytest

from servers.demo_sql_server.db import (
    execute_readonly,
    get_schema,
    init_db,
    validate_readonly,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "demo.db"
    init_db(path)
    return path


def test_init_db_idempotent(db_path: Path) -> None:
    """重复初始化不清空已有数据（幂等）。"""
    init_db(db_path)
    _, rows, _ = execute_readonly(db_path, "SELECT COUNT(*) FROM customers")
    assert rows[0][0] == 5


def test_execute_readonly_returns_columns_and_rows(db_path: Path) -> None:
    columns, rows, truncated = execute_readonly(
        db_path, "SELECT name, city FROM customers ORDER BY id"
    )
    assert columns == ["name", "city"]
    assert rows[0] == ("张伟", "北京")
    assert truncated is False


def test_row_limit_truncation(db_path: Path) -> None:
    _, rows, truncated = execute_readonly(db_path, "SELECT * FROM orders", max_rows=3)
    assert len(rows) == 3
    assert truncated is True


def test_validate_readonly_normalizes_sql() -> None:
    assert validate_readonly("  SELECT 1;  ") == "SELECT 1"
    assert validate_readonly("with t as (select 1) select * from t").upper().startswith("WITH")


@pytest.mark.parametrize(
    "bad_sql",
    [
        "DELETE FROM customers",
        "UPDATE products SET price = 0",
        "DROP TABLE orders",
        "INSERT INTO customers VALUES (9, 'x', 'x', 'x')",
        "SELECT 1; DROP TABLE customers",
        "PRAGMA table_info(customers)",
        "ATTACH DATABASE 'x.db' AS x",
        "",
    ],
)
def test_write_operations_rejected(bad_sql: str) -> None:
    with pytest.raises(ValueError):
        validate_readonly(bad_sql)


def test_column_names_not_mismatched_as_forbidden() -> None:
    """词边界匹配：created_at 等列名不应被误判为危险关键字。"""
    assert validate_readonly("SELECT created_at FROM orders") == "SELECT created_at FROM orders"


def test_get_schema_contains_all_tables(db_path: Path) -> None:
    schema = get_schema(db_path)
    assert "CREATE TABLE " in schema
    for table in ("customers", "products", "orders"):
        assert table in schema
