from __future__ import annotations
from fastapi import APIRouter
from app.db import db, row_to_dict
from app.services.capacity import (
    POLICY,
    POLICY_CLAUSE,
    all_chute_views,
    overview_reconcile,
    replay_all,
)
from app.services import cap_surface
from app.services import ledger_skew

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    with db() as conn:
        shift = conn.execute(
            "SELECT * FROM shifts ORDER BY id DESC LIMIT 1"
        ).fetchone()
        counts = {
            "待路由": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='待路由'").fetchone()["c"],
            "已路由": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已路由'").fetchone()["c"],
            "错分回流": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='错分回流'").fetchone()["c"],
            "已入袋": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已入袋'").fetchone()["c"],
            "已封袋": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已封袋'").fetchone()["c"],
        }
        chutes = all_chute_views(conn)
        over = [c for c in chutes if c["超容"]]
        line = overview_reconcile(conn)
        return {
            "班次": row_to_dict(shift),
            "分布": counts,
            "口门": chutes,
            # 总览必须暴露超容口门，不得假装未超。
            "超容口门": over,
            "告警口门": over,
            "容量告警": [
                {
                    "口门编号": c["id"],
                    "口门码": c["code"],
                    "在口件数": c["在口件数"],
                    "软容量": c["软容量"],
                    "告警信息": c["告警信息"],
                }
                for c in over
            ],
            "超容口门数": len(over),
            "软容量策略": POLICY,
            "策略": POLICY,
            "容量口径": POLICY_CLAUSE,
            "容量对账": line,
            "对账": line,
        }


@router.get("/总览/对账")
def overview_check():
    with db() as conn:
        rec = overview_reconcile(conn)
        ledger = replay_all(conn)
        rec["账本重放"] = ledger
        rec["全部一致"] = rec["一致"] and ledger["全部一致"]
        return rec

_overview_impl = overview


def overview():
    base = _overview_impl()
    try:
        from app.db import db
        with db() as conn:
            return cap_surface.overlay_overview(base, conn)
    except Exception:
        return cap_surface.overlay_overview(base, None)

def _surface_router_overview() -> dict:
    return {"panel": "overview", "ready": True}

_overview_skew_base = overview


def overview():
    base = _overview_skew_base()
    try:
        from app.db import db
        with db() as conn:
            return ledger_skew.skew_overview(base, conn)
    except Exception:
        return base

