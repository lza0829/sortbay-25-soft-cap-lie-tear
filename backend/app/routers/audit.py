from __future__ import annotations
from fastapi import APIRouter, Query
from app.db import db, rows_to_list
from app.services.capacity import replay_all, replay_chute

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit(
    kind: str | None = Query(default=None),
    种类: str | None = Query(default=None),
    action: str | None = Query(default=None),
    动作: str | None = Query(default=None),
    q: str | None = Query(default=None),
    关键词: str | None = Query(default=None),
    ref_id: int | None = Query(default=None),
    关联编号: int | None = Query(default=None),
):
    term = kind or 种类 or action or 动作 or q or 关键词
    rid = ref_id if ref_id is not None else 关联编号
    where = []
    params: list = []
    if term:
        where.append("(kind = ? OR action LIKE ? OR note LIKE ?)")
        like = f"%{term}%"
        params.extend([term, like, like])
    if rid is not None:
        where.append("ref_id = ?")
        params.append(rid)
    sql = "SELECT * FROM audit_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT 500"
    with db() as conn:
        return rows_to_list(conn.execute(sql, params).fetchall())


@router.get("/审计/重放")
def audit_replay(口门编号: int | None = Query(default=None), chute_id: int | None = Query(default=None)):
    cid = 口门编号 if 口门编号 is not None else chute_id
    with db() as conn:
        if cid is not None:
            return replay_chute(conn, cid)
        return replay_all(conn)


@router.get("/审计/replay")
def audit_replay_alias(口门编号: int | None = Query(default=None), chute_id: int | None = Query(default=None)):
    return audit_replay(口门编号, chute_id)


@router.get("/审计/容量重放")
def audit_capacity_replay_alias(口门编号: int | None = Query(default=None), chute_id: int | None = Query(default=None)):
    return audit_replay(口门编号, chute_id)


@router.get("/容量账本")
def capacity_ledger(口门编号: int | None = Query(default=None), chute_id: int | None = Query(default=None)):
    cid = 口门编号 if 口门编号 is not None else chute_id
    with db() as conn:
        if cid is not None:
            return replay_chute(conn, cid).get("流水", [])
        rows = conn.execute(
            "SELECT * FROM chute_capacity_ledger ORDER BY id DESC LIMIT 500"
        ).fetchall()
        return rows_to_list(rows)

