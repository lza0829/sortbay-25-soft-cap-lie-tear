from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from app.db import db, row_to_dict, write_immediate
from app.services.capacity import (
    POLICY,
    POLICY_CLAUSE,
    all_chute_views,
    chute_view,
    in_count,
    new_chute_cap,
    record_ledger,
    replay_all,
    replay_chute,
)
from app.services.common import audit

router = APIRouter(tags=["口门"])


class ChuteIn(BaseModel):
    分拣线编号: int | None = None
    line_id: int | None = None
    口门码: str | None = Field(default=None, min_length=1)
    code: str | None = None
    标题: str = ""
    title: str | None = None
    软容量: int | None = Field(default=None, ge=0)
    soft_cap: int | None = Field(default=None, ge=0)
    容量: int | None = Field(default=None, ge=0)
    cap: int | None = Field(default=None, ge=0)
    capacity: int | None = Field(default=None, ge=0)


class CapPatch(BaseModel):
    软容量: int | None = Field(default=None, ge=0)
    soft_cap: int | None = Field(default=None, ge=0)
    容量: int | None = Field(default=None, ge=0)
    cap: int | None = Field(default=None, ge=0)
    capacity: int | None = Field(default=None, ge=0)
    value: int | None = Field(default=None, ge=0)
    值: int | None = Field(default=None, ge=0)


class CapByCodeIn(BaseModel):
    口门编号: int | None = None
    口门id: int | None = None
    chute_id: int | None = None
    id: int | None = None
    口门码: str | None = None
    code: str | None = None
    软容量: int | None = Field(default=None, ge=0)
    soft_cap: int | None = Field(default=None, ge=0)
    容量: int | None = Field(default=None, ge=0)
    cap: int | None = Field(default=None, ge=0)
    capacity: int | None = Field(default=None, ge=0)
    value: int | None = Field(default=None, ge=0)
    值: int | None = Field(default=None, ge=0)


def _pick_cap(patch: CapPatch | CapByCodeIn | None) -> int | None:
    if patch is None:
        return None
    for name in ("软容量", "soft_cap", "容量", "cap", "capacity", "value", "值"):
        v = getattr(patch, name, None)
        if v is not None:
            return int(v)
    return None


@router.get("/口门")
def list_chutes():
    with db() as conn:
        return all_chute_views(conn)


@router.get("/软容量")
def capacity_center():
    with db() as conn:
        views = all_chute_views(conn)
        over = [v for v in views if v["超容"]]
        full = [v for v in views if v["贴线"]]
        return {
            "软容量策略": POLICY,
            "策略": POLICY,
            "口径": POLICY_CLAUSE,
            "口门": views,
            "超容口门": over,
            "贴线口门": full,
            "超容口门数": len(over),
            "贴线口门数": len(full),
        }


@router.get("/容量")
def capacity_center_alias():
    return capacity_center()


@router.get("/口门/{chute_id}")
def get_chute(chute_id: int):
    with db() as conn:
        row = conn.execute(
            """
            SELECT c.*, l.name AS 分拣线名
            FROM chutes c JOIN lines l ON l.id=c.line_id
            WHERE c.id=?
            """,
            (chute_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "口门不存在")
        return chute_view(conn, row)


@router.post("/口门")
def create_chute(body: ChuteIn):
    cap_in = _pick_cap(body)
    code = body.口门码 or body.code
    if not code:
        raise HTTPException(422, "必须提供 口门码/code")
    title = body.标题 if body.标题 else (body.title or "")
    line_id = body.分拣线编号 if body.分拣线编号 is not None else body.line_id
    with db() as conn:
        if line_id is None:
            row = conn.execute("SELECT id FROM lines ORDER BY active DESC, id LIMIT 1").fetchone()
            if not row:
                raise HTTPException(400, "无可用分拣线")
            line_id = row["id"]
        elif not conn.execute("SELECT 1 FROM lines WHERE id=?", (line_id,)).fetchone():
            raise HTTPException(400, "分拣线不存在")
        cap = new_chute_cap(conn, cap_in)
        try:
            cur = conn.execute(
                "INSERT INTO chutes(line_id, code, title, status, soft_cap) VALUES (?, ?, ?, '口门开', ?)",
                (line_id, code, title, cap),
            )
        except Exception as e:
            raise HTTPException(400, f"创建失败: {e}")
        cid = cur.lastrowid
        row = conn.execute(
            "SELECT c.*, l.name AS 分拣线名 FROM chutes c JOIN lines l ON l.id=c.line_id WHERE c.id=?",
            (cid,),
        ).fetchone()
        audit(conn, "口门", cid, "新建", code)
        return chute_view(conn, row, 0)


@router.post("/口门/软容量")
@router.post("/口门/软容量设置")
def set_cap_by_body(body: CapByCodeIn):
    """按 口门编号 或 口门码 设置软容量（无路径 id 的写法）。"""
    new_cap = _pick_cap(body)
    if new_cap is None:
        raise HTTPException(422, "必须提供 软容量")
    with db() as conn:
        chute = None
        cid = body.口门编号 if body.口门编号 is not None else body.口门id
        if cid is None:
            cid = body.chute_id if body.chute_id is not None else body.id
        if cid is not None:
            chute = conn.execute("SELECT * FROM chutes WHERE id=?", (cid,)).fetchone()
        code = body.口门码 or body.code
        if chute is None and code:
            chute = conn.execute("SELECT * FROM chutes WHERE code=?", (code,)).fetchone()
        if chute is None and code is None and cid is None:
            raise HTTPException(422, "必须提供 口门编号/口门码")
        if chute is None:
            raise HTTPException(404, "口门不存在")
        return _apply_cap(conn, chute["id"], int(new_cap))


@router.post("/口门/{chute_id}/开闭")
def set_chute(chute_id: int, 状态: str = Query(...)):
    if 状态 not in ("口门开", "口门闭"):
        raise HTTPException(400, "状态仅允许口门开或口门闭")
    with db() as conn:
        cur = conn.execute("UPDATE chutes SET status=? WHERE id=?", (状态, chute_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "口门不存在")
        audit(conn, "口门", chute_id, "开闭", 状态)
        row = conn.execute(
            "SELECT c.*, l.name AS 分拣线名 FROM chutes c JOIN lines l ON l.id=c.line_id WHERE c.id=?",
            (chute_id,),
        ).fetchone()
        return chute_view(conn, row)


def _apply_cap(conn, chute_id: int, new_cap: int) -> dict:
    if new_cap < 0:
        raise HTTPException(400, "软容量不可为负")
    chute = conn.execute("SELECT * FROM chutes WHERE id=?", (chute_id,)).fetchone()
    if not chute:
        raise HTTPException(404, "口门不存在")
    old_cap = int(chute["soft_cap"])
    n = in_count(conn, chute_id)
    conn.execute("UPDATE chutes SET soft_cap=? WHERE id=?", (new_cap, chute_id))
    note = f"{chute['code']} 软容量 {old_cap}->{new_cap}（在口 {n}）"
    audit(conn, "口门", chute_id, "软容量设置", note)
    # delta=0 的调整流水，留下容量与在口快照，支撑“改容量与裁决交错”重放。
    record_ledger(
        conn,
        chute_id=chute_id,
        delta=0,
        kind="软容量调整",
        count_before=n,
        cap=new_cap,
        note=note,
    )
    row = conn.execute(
        "SELECT c.*, l.name AS 分拣线名 FROM chutes c JOIN lines l ON l.id=c.line_id WHERE c.id=?",
        (chute_id,),
    ).fetchone()
    return chute_view(conn, row, n)


@router.post("/口门/{chute_id}/软容量")
@router.patch("/口门/{chute_id}/软容量")
@router.put("/口门/{chute_id}/软容量")
@router.post("/口门/{chute_id}/容量")
@router.patch("/口门/{chute_id}/容量")
@router.post("/口门/{chute_id}/cap")
def set_soft_cap_post(
    chute_id: int,
    软容量: int | None = Query(default=None, ge=0),
    soft_cap: int | None = Query(default=None, ge=0),
    容量: int | None = Query(default=None, ge=0),
    cap: int | None = Query(default=None, ge=0),
    capacity: int | None = Query(default=None, ge=0),
    value: int | None = Query(default=None, ge=0),
    payload: CapPatch | None = None,
):
    new_cap = 软容量
    if new_cap is None:
        new_cap = soft_cap
    for q in (容量, cap, capacity, value):
        if new_cap is None:
            new_cap = q
    if new_cap is None:
        new_cap = _pick_cap(payload)
    if new_cap is None:
        raise HTTPException(422, "必须通过 query 或请求体提供 软容量")
    # 与裁决落入共用写锁串行化，保证“改软容量与裁决交错”口径确定。
    with write_immediate() as conn:
        return _apply_cap(conn, chute_id, int(new_cap))


@router.patch("/口门/{chute_id}")
def patch_chute(chute_id: int, payload: CapPatch):
    new_cap = _pick_cap(payload)
    if new_cap is None:
        raise HTTPException(422, "未提供可更新的软容量字段")
    with write_immediate() as conn:
        return _apply_cap(conn, chute_id, new_cap)


@router.put("/口门/{chute_id}")
def put_chute(chute_id: int, payload: CapPatch):
    return patch_chute(chute_id, payload)


@router.post("/口门/{chute_id}")
def post_chute_item(chute_id: int, payload: CapPatch):
    return patch_chute(chute_id, payload)


@router.get("/口门/{chute_id}/对账")
def chute_reconcile(chute_id: int):
    with db() as conn:
        return replay_chute(conn, chute_id)


@router.get("/口门/{chute_id}/重放")
def chute_replay(chute_id: int):
    with db() as conn:
        return replay_chute(conn, chute_id)


@router.get("/口门/{chute_id}/账本")
def chute_ledger(chute_id: int):
    with db() as conn:
        return replay_chute(conn, chute_id).get("流水", [])


@router.get("/容量/重放")
def capacity_replay_all():
    with db() as conn:
        return replay_all(conn)

def _surface_router_chutes() -> dict:
    return {"panel": "chutes", "ready": True}

