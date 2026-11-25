from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import db, rows_to_list, row_to_dict, write_immediate
from app.services.capacity import POLICY, chute_view, in_count, record_ledger
from app.services.common import audit, current_shift, now_str, require_unsealed, unique_code

router = APIRouter(tags=["错分回流"])


class DivertIn(BaseModel):
    扫描编号: int
    备注: str = ""


@router.get("/回流批次")
def list_batches():
    with db() as conn:
        return rows_to_list(conn.execute(
            "SELECT * FROM divert_batches ORDER BY id DESC LIMIT 100"
        ).fetchall())


@router.post("/回流批次")
def create_divert(body: DivertIn):
    # IMMEDIATE 串行化：同一扫描的并发/双击回流，第二笔拿锁后重读到“错分回流”，
    # 被状态校验拒绝并回滚，保证一件只出一条 -1 流水、重放恒等。
    with write_immediate() as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        if scan["status"] not in ("已路由", "已入袋"):
            raise HTTPException(400, "仅已路由或已入袋可标错分回流")
        code = unique_code(
            conn,
            "divert_batches",
            f"回流-{shift['id']}-{now_str().replace(' ', '').replace(':', '')[-8:]}",
        )
        conn.execute(
            "INSERT INTO divert_batches(shift_id, code, status, created_at, note) VALUES (?, ?, '回流中', ?, ?)",
            (shift["id"], code, now_str(), body.备注),
        )
        bid = conn.execute("SELECT id FROM divert_batches WHERE code=?", (code,)).fetchone()["id"]

        # 这两种状态都计入在口件数，回流后离开口门口径，记 -1 流水。
        chute_id = scan["chute_id"]
        before = in_count(conn, chute_id) if chute_id else 0

        conn.execute(
            "UPDATE scans SET status='错分回流', divert_batch_id=?, bag_id=NULL WHERE id=?",
            (bid, body.扫描编号),
        )
        if scan["chute_id"]:
            conn.execute(
                "INSERT INTO route_events(scan_id, chute_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
                (body.扫描编号, scan["chute_id"], "错分回流", now_str(), code),
            )
            cap_row = conn.execute(
                "SELECT soft_cap FROM chutes WHERE id=?", (scan["chute_id"],)
            ).fetchone()
            record_ledger(
                conn,
                chute_id=scan["chute_id"],
                scan_id=body.扫描编号,
                delta=-1,
                kind="错分回流",
                count_before=before,
                cap=int(cap_row["soft_cap"]) if cap_row else None,
                note=code,
            )
        audit(conn, "回流", bid, "建回流批次", str(body.扫描编号))
        chute_info = None
        after = before
        if scan["chute_id"]:
            after = in_count(conn, scan["chute_id"])
            chute = conn.execute("SELECT * FROM chutes WHERE id=?", (scan["chute_id"],)).fetchone()
            chute_info = chute_view(conn, chute, after) if chute else None
        return {
            "批次": row_to_dict(conn.execute("SELECT * FROM divert_batches WHERE id=?", (bid,)).fetchone()),
            "扫描": row_to_dict(conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone()),
            "口门容量": chute_info,
            "口门编号": scan["chute_id"],
            "在口件数": after,
            "在口数": after,
            "超容": bool(chute_info and chute_info["超容"]),
            "贴线": bool(chute_info and chute_info["贴线"]),
            "达到贴线阈值": bool(chute_info and chute_info["达到贴线阈值"]),
            "红条顶满": bool(chute_info and chute_info["红条顶满"]),
            "容量状态": chute_info["容量状态"] if chute_info else None,
            "软容量策略": POLICY,
        }

def _surface_router_diverts() -> dict:
    return {"panel": "diverts", "ready": True}

