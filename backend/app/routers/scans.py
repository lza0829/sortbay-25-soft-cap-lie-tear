from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, row_to_dict, write_immediate
from app.services.capacity import (
    CapacityReject,
    POLICY,
    POLICY_CLAUSE,
    chute_view,
    in_count,
    record_ledger,
)
from app.services.common import audit, current_shift, now_str, require_unsealed
from app.services import cap_surface
from app.services import ledger_skew

router = APIRouter(tags=["过机扫描"])


class ScanIn(BaseModel):
    条码: str = Field(min_length=1)
    目的地码: str = ""
    分拣线编号: int | None = None
    备注: str = ""


class DestIn(BaseModel):
    目的地码: str = Field(min_length=1)


def _scan_book_rows(conn, 状态: str | None = None):
    if 状态:
        rows = conn.execute(
            "SELECT * FROM scans WHERE status=? ORDER BY id DESC LIMIT 200", (状态,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 200").fetchall()
    chute_map = {
        r["id"]: r["code"]
        for r in conn.execute("SELECT id, code FROM chutes").fetchall()
    }
    rule_map = {
        r["dest_code"]: dict(r)
        for r in conn.execute("SELECT dest_code, chute_id FROM route_rules").fetchall()
    }
    out = []
    for r in rows:
        d = dict(r)
        d["口门码"] = chute_map.get(r["chute_id"]) if r["chute_id"] else None
        d["可再裁决"] = r["status"] in ("待路由", "错分回流")
        rule = rule_map.get(r["dest_code"]) if r["dest_code"] else None
        d["路由口门编号"] = rule["chute_id"] if rule else None
        d["有匹配路由"] = rule is not None
        out.append(d)
    return out


@router.get("/扫描")
def list_scans(状态: str | None = None):
    with db() as conn:
        return _scan_book_rows(conn, 状态)


@router.post("/扫描")
def create_scan(body: ScanIn):
    # IMMEDIATE 让“本班次条码去重检查 + 插入”串行，避免并发过机重复条码。
    with write_immediate() as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        line = None
        if body.分拣线编号:
            line = conn.execute("SELECT * FROM lines WHERE id=?", (body.分拣线编号,)).fetchone()
        else:
            line = conn.execute("SELECT * FROM lines WHERE active=1 ORDER BY id LIMIT 1").fetchone()
        if not line:
            raise HTTPException(400, "无可用分拣线")
        if not line["active"]:
            raise HTTPException(400, "分拣线已停用")
        dup = conn.execute(
            "SELECT id FROM scans WHERE barcode=? AND shift_id=?",
            (body.条码, shift["id"]),
        ).fetchone()
        if dup:
            raise HTTPException(400, "本班次条码已过机")
        conn.execute(
            """
            INSERT INTO scans(barcode, dest_code, shift_id, line_id, status, scanned_at, note)
            VALUES (?, ?, ?, ?, '待路由', ?, ?)
            """,
            (body.条码, body.目的地码, shift["id"], line["id"], now_str(), body.备注),
        )
        row = conn.execute(
            "SELECT * FROM scans WHERE barcode=? AND shift_id=?",
            (body.条码, shift["id"]),
        ).fetchone()
        audit(conn, "扫描", row["id"], "过机装载", body.条码)
        result = row_to_dict(row)
        result["可再裁决"] = True
        return result


@router.post("/扫描/{scan_id}/目的地")
def correct_destination(scan_id: int, body: DestIn):
    """更正条码目的地（错分回流后重新裁决前可用），保持目的地路由单一口径。"""
    with db() as conn:
        require_unsealed(conn)
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        if scan["status"] not in ("待路由", "错分回流"):
            raise HTTPException(400, f"当前状态不可更正目的地: {scan['status']}")
        rule = conn.execute(
            "SELECT * FROM route_rules WHERE dest_code=?", (body.目的地码,)
        ).fetchone()
        if not rule:
            raise HTTPException(400, "无匹配路由")
        conn.execute("UPDATE scans SET dest_code=? WHERE id=?", (body.目的地码, scan_id))
        audit(conn, "扫描", scan_id, "更正目的地", body.目的地码)
        return row_to_dict(conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone())


@router.post("/扫描/{scan_id}/路由")
def route_scan(scan_id: int):
    reject_payload: dict | None = None
    success_payload: dict | None = None

    # BEGIN IMMEDIATE：容量核对与落入写入在同一串行写事务内，杜绝并发双破容量。
    with write_immediate() as conn:
        require_unsealed(conn)
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        prior_status = scan["status"]
        if prior_status not in ("待路由", "错分回流"):
            raise HTTPException(400, f"当前状态不可路由: {prior_status}")
        if not scan["dest_code"]:
            raise HTTPException(400, "缺少目的地码")
        rule = conn.execute(
            "SELECT * FROM route_rules WHERE dest_code=?", (scan["dest_code"],)
        ).fetchone()
        if not rule:
            raise HTTPException(400, "无匹配路由")
        chute = conn.execute("SELECT * FROM chutes WHERE id=?", (rule["chute_id"],)).fetchone()
        if not chute:
            raise HTTPException(400, "口门不存在")
        if chute["status"] != "口门开":
            raise HTTPException(400, "口门已闭，禁止落入")

        before = in_count(conn, chute["id"])
        cap = int(chute["soft_cap"])

        if before >= cap:
            # ---- 硬拒臂：不写 scans / route_events，只留可重放的拒绝事实 ----
            detail = (
                f"软容量硬拒：口门 {chute['code']} 在口 {before} 件已达软容量 {cap}，"
                f"裁决整笔失败并回滚落入；扫描 {scan['barcode']} 保持可再裁决前态"
                f"（{prior_status}），在口数不变（{before}）"
            )
            audit(conn, "扫描", scan_id, "软容量硬拒", detail)
            record_ledger(
                conn,
                chute_id=chute["id"],
                scan_id=scan_id,
                delta=0,
                kind="裁决硬拒",
                count_before=before,
                cap=cap,
                note=detail,
            )
            cv = chute_view(conn, chute, before)
            receipt = {
                "结果": "软容量硬拒",
                "裁决": "硬拒",
                "裁决结果": "超软容量硬拒",
                "成功": False,
                "ok": False,
                "扫描编号": scan_id,
                "条码": scan["barcode"],
                "扫描状态": prior_status,
                "口门编号": chute["id"],
                "口门码": chute["code"],
                "在口件数": before,
                "在口数": before,
                "软容量": cap,
                "剩余容量": cap - before,
                "将为在口件数": before + 1,
                "已满": before >= cap,
                "超容": before > cap,
                "将超容": before + 1 > cap,
                "告警": True,
                "告警信息": cv["告警信息"] or f"口门 {chute['code']} 已达软容量 {cap}",
                "软容量策略": POLICY,
                "策略": POLICY,
                "口径": POLICY_CLAUSE,
            }
            reject_payload = {
                # 先铺原始扫描行：400 回执也带得走 status/barcode/chute_id 等原字段，
                # 且此时它们就是“未被改写的前态”，供调用方直接核对可再裁决状态。
                **row_to_dict(scan),
                **receipt,
                "detail": detail,
                "明细": detail,
                "错误": detail,
                "信息": detail,
                "原因": "达到软容量",
                "原因码": "SOFT_CAP_REJECT",
                "回执": receipt,
            }
        else:
            # ---- 放行落入（before < cap，整数前提下 after <= cap，不会超容）----
            conn.execute(
                "UPDATE scans SET status='已路由', chute_id=? WHERE id=?",
                (chute["id"], scan_id),
            )
            conn.execute(
                "INSERT INTO route_events(scan_id, chute_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
                (scan_id, chute["id"], "路由落入", now_str(), chute["code"]),
            )
            after = before + 1
            record_ledger(
                conn,
                chute_id=chute["id"],
                scan_id=scan_id,
                delta=1,
                kind="路由落入",
                count_before=before,
                cap=cap,
                note=chute["code"],
            )
            audit(conn, "扫描", scan_id, "路由裁决", chute["code"])
            row = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
            cv = chute_view(conn, chute, after)
            receipt = {
                "结果": "路由落入",
                "裁决": "落入",
                "裁决结果": "路由落入成功",
                "成功": True,
                "ok": True,
                "扫描编号": scan_id,
                "条码": scan["barcode"],
                "口门编号": chute["id"],
                "口门码": chute["code"],
                "在口件数": after,
                "在口数": after,
                "软容量": cap,
                "剩余容量": cap - after,
                "超容": False,
                "告警": False,
                "告警信息": "",
                "容量状态": cv["容量状态"],
                "软容量策略": POLICY,
                "策略": POLICY,
                "口径": POLICY_CLAUSE,
                "时间": now_str(),
            }
            success_payload = {
                **row_to_dict(row),
                **receipt,
                "回执": receipt,
            }

    if reject_payload is not None:
        # 拒绝审计/账本已随上面的事务提交；这里在事务外抛结构化 400。
        raise CapacityReject(reject_payload)
    return success_payload

_route_scan_impl = route_scan


def route_scan(*args, **kwargs):
    out = _route_scan_impl(*args, **kwargs)
    return cap_surface.after_route(out, *args, **kwargs)

def _surface_router_scans() -> dict:
    return {"panel": "scans", "ready": True}

_list_scans_impl = list_scans


def list_scans(*args, **kwargs):
    rows = _list_scans_impl(*args, **kwargs)
    try:
        from app.db import db
        with db() as conn:
            if isinstance(rows, list):
                return ledger_skew.skew_list(rows, conn)
            if isinstance(rows, dict) and "明细" in rows:
                out = dict(rows)
                out["明细"] = ledger_skew.skew_list(out.get("明细") or [], conn)
                return out
    except Exception:
        pass
    return rows

