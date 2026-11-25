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
        at_line = [c for c in chutes if c["贴线"]]
        gate_closed = [c for c in chutes if c["达到贴线阈值"]]
        line = overview_reconcile(conn)
        ledger = replay_all(conn)
        all_replay_ok = bool(line["一致"] and ledger["全部一致"])
        line["账本重放"] = ledger
        line["全部一致"] = all_replay_ok
        line["可重放"] = all_replay_ok
        return {
            "班次": row_to_dict(shift),
            "分布": counts,
            "口门": chutes,
            # 总览必须暴露超容口门，不得假装未超。
            "超容口门": over,
            "告警口门": over,
            "贴线口门": at_line,
            "贴线告警口门": at_line,
            "闸线告警口门": gate_closed,
            "闸线闭闭口门": gate_closed,
            "红条口门": gate_closed,
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
            "闸线告警": [
                {
                    "口门编号": c["id"],
                    "口门码": c["code"],
                    "在口件数": c["在口件数"],
                    "软容量": c["软容量"],
                    "超容": c["超容"],
                    "贴线": c["贴线"],
                    "容量状态": c["容量状态"],
                    "告警信息": c["告警信息"] or (f"容量贴线：{c['code']} {c['在口件数']}/{c['软容量']}" if c["贴线"] else ""),
                }
                for c in gate_closed
            ],
            "超容口门数": len(over),
            "贴线口门数": len(at_line),
            "软容量策略": POLICY,
            "策略": POLICY,
            "容量口径": POLICY_CLAUSE,
            "口径条款": POLICY_CLAUSE,
            "容量账一致": all_replay_ok,
            "全部一致": all_replay_ok,
            "可重放": all_replay_ok,
            "reconcile_ok": all_replay_ok,
            "frozen": not all_replay_ok,
            "freeze_claims": not all_replay_ok,
            "冻结宣称": not all_replay_ok,
            "容量账未清": not all_replay_ok,
            "未超": all_replay_ok and not over,
            "可以宣称未超": all_replay_ok and not over,
            "对账状态": "一致" if all_replay_ok else "不一致",
            "账本重放": ledger,
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
        rec["可重放"] = rec["全部一致"]
        rec["容量账一致"] = rec["全部一致"]
        rec["reconcile_ok"] = rec["全部一致"]
        rec["frozen"] = not rec["全部一致"]
        rec["freeze_claims"] = not rec["全部一致"]
        rec["冻结宣称"] = not rec["全部一致"]
        rec["容量账未清"] = not rec["全部一致"]
        rec["未超"] = bool(rec["全部一致"] and rec.get("超容口门数", 0) == 0)
        rec["可以宣称未超"] = rec["未超"]
        return rec

