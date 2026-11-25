from __future__ import annotations
from datetime import datetime
from fastapi import HTTPException
from app.db import db, row_to_dict


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def unique_code(conn, table: str, base: str) -> str:
    """在指定表的 code 列上返回一个不冲突的编码。

    原始批次/袋编码只精确到秒，快速连续建单会撞 UNIQUE(code)。优先原样返回
    基准编码（保持既有格式/断言），仅在已存在时追加 -2/-3… 消歧。调用方应在
    IMMEDIATE 写事务内使用，从而与插入一起串行，杜绝并发同码。
    `table` 只允许调用方传入的内部常量，不接受最终用户输入。
    """
    candidate = base
    n = 2
    while conn.execute(
        f"SELECT 1 FROM {table} WHERE code=? LIMIT 1", (candidate,)
    ).fetchone():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def current_shift(conn):
    row = conn.execute(
        "SELECT * FROM shifts WHERE sealed=0 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise HTTPException(400, "无未封班班次")
    return row


def audit(conn, kind: str, ref_id: int | None, action: str, note: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_events(kind, ref_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
        (kind, ref_id, action, now_str(), note),
    )


def require_unsealed(conn) -> None:
    s = current_shift(conn)
    if s["sealed"]:
        raise HTTPException(400, "班次已封班，禁止写口")
