from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import db, rows_to_list, row_to_dict, write_immediate
from app.services.capacity import chute_view, in_count, in_counts_bulk, record_ledger
from app.services.common import audit, current_shift, now_str, require_unsealed, unique_code

router = APIRouter(tags=["封袋"])


class BagOpenIn(BaseModel):
    口门编号: int
    备注: str = ""


class BagAddIn(BaseModel):
    扫描编号: int


def _bag_row_with_cap(conn, b) -> dict:
    d = dict(b)
    chute = conn.execute("SELECT * FROM chutes WHERE id=?", (b["chute_id"],)).fetchone()
    if chute:
        cv = chute_view(conn, chute)
        for k in (
            "软容量", "在口件数", "在口数", "剩余容量", "超容", "是否超容",
            "容量状态", "压力等级", "告警", "告警信息", "告警级别", "软容量策略",
        ):
            d[k] = cv[k]
    return d


@router.get("/封袋")
def list_bags():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT b.*, c.code AS 口门码, c.soft_cap AS soft_cap,
              (SELECT COUNT(*) FROM scans s WHERE s.bag_id=b.id) AS 件数
            FROM bags b JOIN chutes c ON c.id=b.chute_id
            ORDER BY b.id DESC LIMIT 100
            """
        ).fetchall()
        counts = in_counts_bulk(conn)
        out = []
        for b in rows:
            d = dict(b)
            cid = b["chute_id"]
            n = counts.get(cid, 0)
            cap = int(b["soft_cap"])
            d["在口件数"] = n
            d["在口数"] = n
            d["软容量"] = cap
            d["超容"] = n > cap
            d["告警"] = n > cap
            d["容量状态"] = "超容" if n > cap else ("贴线" if n >= cap else "正常")
            d["集包压力"] = d["容量状态"]
            out.append(d)
        return out


@router.post("/封袋/开袋")
def open_bag(body: BagOpenIn):
    # IMMEDIATE + unique_code：同口门同班快速/并发开袋时，秒级时间戳撞码也不会 500。
    with write_immediate() as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        chute = conn.execute("SELECT * FROM chutes WHERE id=?", (body.口门编号,)).fetchone()
        if not chute:
            raise HTTPException(404, "口门不存在")
        base = f"袋-{chute['code']}-{shift['id']}-{now_str().replace(':','').replace(' ', '')[-10:]}"
        code = unique_code(conn, "bags", base)
        conn.execute(
            "INSERT INTO bags(shift_id, chute_id, code, status, note) VALUES (?, ?, ?, '集包中', ?)",
            (shift["id"], body.口门编号, code, body.备注),
        )
        row = conn.execute("SELECT * FROM bags WHERE code=?", (code,)).fetchone()
        audit(conn, "封袋", row["id"], "开袋", code)
        # 软容量闸不拦开袋：超容口门更需要开袋集包泄压。仅把超容事实带进回执。
        result = _bag_row_with_cap(conn, row)
        result["口门容量"] = chute_view(conn, chute)
        result["超容口仍可开袋"] = True
        return result


@router.post("/封袋/{bag_id}/入袋")
def add_to_bag(bag_id: int, body: BagAddIn):
    # IMMEDIATE：同一扫描并发入两个袋/重复入袋时，第二笔在锁内重读到“已入袋”，
    # 被“仅已路由件可入袋”拒绝，避免 bag_id 互相覆盖导致件数/集包口径拧花。
    with write_immediate() as conn:
        require_unsealed(conn)
        bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
        if not bag:
            raise HTTPException(404, "袋不存在")
        if bag["status"] != "集包中":
            raise HTTPException(400, "袋已封，禁止再入")
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        if scan["status"] != "已路由":
            raise HTTPException(400, "仅已路由件可入袋")
        if scan["chute_id"] != bag["chute_id"]:
            raise HTTPException(400, "扫描口门与袋口门不一致")
        conn.execute(
            "UPDATE scans SET status='已入袋', bag_id=? WHERE id=?",
            (bag_id, body.扫描编号),
        )
        audit(conn, "封袋", bag_id, "入袋", str(body.扫描编号))
        result = row_to_dict(conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone())
        # 已路由 -> 已入袋 同属在口口径，在口件数不变；附上容量压力供封袋页同刷。
        chute = conn.execute("SELECT * FROM chutes WHERE id=?", (bag["chute_id"],)).fetchone()
        if chute:
            cv = chute_view(conn, chute)
            result["在口件数"] = cv["在口件数"]
            result["软容量"] = cv["软容量"]
            result["超容"] = cv["超容"]
            result["容量状态"] = cv["容量状态"]
        return result


@router.post("/封袋/{bag_id}/封口")
def seal_bag(bag_id: int):
    # IMMEDIATE：同一袋并发/双击封口时，第二笔在锁内重读到“已封袋”并被拒，
    # 保证封口负差流水只记一次、SUM(delta) 恒等于在口数。
    with write_immediate() as conn:
        require_unsealed(conn)
        bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
        if not bag:
            raise HTTPException(404, "袋不存在")
        if bag["status"] != "集包中":
            raise HTTPException(400, "袋状态不可封口")
        n = conn.execute(
            "SELECT COUNT(*) c FROM scans WHERE bag_id=?", (bag_id,)
        ).fetchone()["c"]
        if n == 0:
            raise HTTPException(400, "空袋不可封口")
        chute_id = bag["chute_id"]
        before = in_count(conn, chute_id)
        conn.execute(
            "UPDATE bags SET status='已封袋', sealed_at=? WHERE id=?",
            (now_str(), bag_id),
        )
        conn.execute(
            "UPDATE scans SET status='已封袋' WHERE bag_id=?",
            (bag_id,),
        )
        after = in_count(conn, chute_id)
        chute = conn.execute("SELECT * FROM chutes WHERE id=?", (chute_id,)).fetchone()
        cap = int(chute["soft_cap"]) if chute else None
        # 封口后整袋离格：在口口径移除 n 件，记 -n（以实际前后差值为准）。
        record_ledger(
            conn,
            chute_id=chute_id,
            scan_id=None,
            delta=after - before,
            kind="封袋移出",
            count_before=before,
            cap=cap,
            note=f"袋 {bag['code']} 封口件数{n}",
        )
        audit(conn, "封袋", bag_id, "封口", f"件数{n}")
        result = row_to_dict(conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone())
        if chute:
            result["口门容量"] = chute_view(conn, chute, after)
            result["在口件数"] = after
            result["超容"] = after > cap if cap is not None else False
        return result

def _surface_router_bags() -> dict:
    return {"panel": "bags", "ready": True}

