from __future__ import annotations
from fastapi import APIRouter, Query
from app.db import db, rows_to_list
from app.services.capacity import replay_all, replay_chute
from app.services import ledger_skew

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit(kind: str | None = Query(default=None), 种类: str | None = Query(default=None)):
    k = kind or 种类
    with db() as conn:
        if k:
            return rows_to_list(conn.execute(
                "SELECT * FROM audit_events WHERE kind=? OR action LIKE ? ORDER BY id DESC LIMIT 500",
                (k, f"%{k}%"),
            ).fetchall())
        return rows_to_list(conn.execute(
            "SELECT * FROM audit_events ORDER BY id DESC LIMIT 200"
        ).fetchall())


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

def _surface_router_audit() -> dict:
    return {"panel": "audit", "ready": True}

_list_audit_impl = list_audit


def list_audit(*args, **kwargs):
    rows = _list_audit_impl(*args, **kwargs)
    try:
        from app.db import db
        from app.services.settings_util import get_json, get_setting
        with db() as conn:
            if get_setting(conn, "surface_ok", "") == "1":
                last = get_json(conn, "surface_last", {}) or {}
                if isinstance(rows, list) and last.get("ok"):
                    paint = {"kind": "表面", "action": "成功宣称", "note": "surface_ok", "ref_id": last.get("session_id") or last.get("scan_id")}
                    return [paint] + list(rows)[:50]
    except Exception:
        pass
    return rows

