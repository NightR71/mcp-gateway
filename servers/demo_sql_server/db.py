"""演示业务库：SQLite 初始化、种子数据与只读查询。

- 演示场景为迷你电商：customers / products / orders 三张表；
- 所有对外查询强制只读（SELECT/WITH 单语句），写操作一律拒绝；
- 标准库 sqlite3 实现，无额外依赖；本模块为同步代码，
  由 MCP Server 的 async 工具包装调用（演示数据量极小，耗时可忽略）。
"""

import re
import sqlite3
from pathlib import Path
from typing import Any

# 写操作/危险关键字（词边界匹配，避免误伤 created_at 这类列名）
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|vacuum|pragma|attach"
    r"|detach|reindex)\b",
    re.IGNORECASE,
)

DEFAULT_MAX_ROWS = 50

_DDL = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    vip_level TEXT NOT NULL DEFAULT '普通'
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price REAL NOT NULL,
    stock INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL,
    amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT '已完成',
    created_at TEXT NOT NULL
);
"""

_CUSTOMERS = [
    (1, "张伟", "北京", "金牌"),
    (2, "李娜", "上海", "银牌"),
    (3, "王强", "北京", "普通"),
    (4, "刘洋", "深圳", "金牌"),
    (5, "陈静", "杭州", "银牌"),
]

_PRODUCTS = [
    (1, "机械键盘", "外设", 299.0, 45),
    (2, "无线鼠标", "外设", 99.0, 120),
    (3, "27寸显示器", "显示器", 1299.0, 8),
    (4, "笔记本电脑", "整机", 5499.0, 15),
    (5, "人体工学椅", "家具", 1899.0, 3),
]

_ORDERS = [
    (1, 1, 4, 1, 5499.0, "已完成", "2026-07-02 10:15:00"),
    (2, 2, 1, 2, 598.0, "已完成", "2026-07-05 14:22:00"),
    (3, 3, 2, 1, 99.0, "已取消", "2026-07-09 09:03:00"),
    (4, 1, 3, 1, 1299.0, "已完成", "2026-07-18 16:40:00"),
    (5, 4, 4, 2, 10998.0, "已完成", "2026-07-25 11:08:00"),
    (6, 5, 5, 1, 1899.0, "待发货", "2026-08-01 20:31:00"),
    (7, 2, 3, 1, 1299.0, "已完成", "2026-08-06 13:57:00"),
    (8, 4, 1, 1, 299.0, "已完成", "2026-08-11 08:45:00"),
    (9, 3, 2, 3, 297.0, "已完成", "2026-08-14 19:26:00"),
]


def init_db(db_path: str | Path) -> None:
    """建表并写入种子数据（表内已有数据则跳过，幂等）。"""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_DDL)
        (count,) = conn.execute("SELECT COUNT(*) FROM customers").fetchone()
        if count == 0:
            conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?)", _CUSTOMERS)
            conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?)", _PRODUCTS)
            conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)", _ORDERS)
        conn.commit()
    finally:
        conn.close()


def validate_readonly(sql: str) -> str:
    """校验 SQL 为只读单语句，返回归一化后的 SQL；违规抛 ValueError。"""
    normalized = sql.strip().rstrip(";").strip()
    if not normalized:
        raise ValueError("SQL 不能为空")
    if not normalized.upper().startswith(("SELECT", "WITH")):
        raise ValueError("只允许 SELECT / WITH 只读查询")
    if ";" in normalized:
        raise ValueError("不允许多条语句（分号）")
    match = _FORBIDDEN.search(normalized)
    if match:
        raise ValueError("不允许包含写操作/危险关键字: " + match.group(0))
    return normalized


def execute_readonly(
    db_path: str | Path, sql: str, max_rows: int = DEFAULT_MAX_ROWS
) -> tuple[list[str], list[tuple[Any, ...]], bool]:
    """执行只读查询，返回 (列名, 行数据, 是否被行数上限截断)。"""
    normalized = validate_readonly(sql)
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(normalized)
        columns = [d[0] for d in cursor.description]
        fetched = cursor.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        return columns, fetched[:max_rows], truncated
    finally:
        conn.close()


def get_schema(db_path: str | Path) -> str:
    """返回全部业务表的建表语句（供 list_tables 工具展示）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return "\n\n".join(row[0] for row in rows)
    finally:
        conn.close()
