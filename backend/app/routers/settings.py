from __future__ import annotations
from typing import Any
from fastapi import APIRouter
from pydantic import BaseModel
from app.db import db, rows_to_list, write_immediate
from app.services.capacity import (
    GLOBAL_CAP_KEYS,
    POLICY,
    POLICY_CLAUSE,
    in_counts_bulk,
    record_ledger,
)
from app.services.common import audit

router = APIRouter(tags=["设置"])


class SettingIn(BaseModel):
    键: str
    值: Any = ""


@router.get("/设置")
def list_settings():
    with db() as conn:
        rows = rows_to_list(conn.execute("SELECT key AS 键, value AS 值 FROM settings").fetchall())
        for r in rows:
            key = str(r["键"])
            low = key.lower()
            if (
                key == "软容量策略"
                or "策略" in key
                or "架构" in key
                or low in ("policy", "architecture")
                or "policy" in low
                or "architecture" in low
            ):
                r["值"] = POLICY  # 全局唯一口径，禁止经设置切成第二种架构
            elif "口径" in key:
                r["值"] = POLICY_CLAUSE
        return rows


@router.post("/设置")
def set_setting(body: SettingIn):
    raw = body.值
    value = raw if isinstance(raw, str) else str(raw)
    synced = 0
    policy_key = (
        body.键 in ("软容量策略", "超容策略", "容量策略", "超软容量口径")
        or "策略" in body.键
        or "架构" in body.键
        or body.键.lower() in ("policy", "architecture")
    )
    clause_key = "口径" in body.键
    if policy_key:
        # 全局唯一口径：任何策略键都归一到已选定的硬拒臂，杜绝两种架构混用。
        value = POLICY
    elif clause_key:
        value = POLICY_CLAUSE
    # 与裁决/单口门容量调整共用 BEGIN IMMEDIATE 写锁，避免全局下调与落入交错。
    with write_immediate() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (body.键, value),
        )
        audit(conn, "设置", None, "改设置", f"{body.键}={value}")

        # 软容量全局设置：落设置行的同时批量同步每一个口门，保证“软容量设置”
        # 真正参与裁决闸，而不是只改一行提示字。
        if body.键 in GLOBAL_CAP_KEYS:
            try:
                cap = int(value.strip())
            except (TypeError, ValueError):
                cap = None
            if cap is not None and cap >= 0:
                chutes = conn.execute("SELECT id, code FROM chutes ORDER BY id").fetchall()
                counts = in_counts_bulk(conn)
                cur = conn.execute("UPDATE chutes SET soft_cap=?", (cap,))
                synced = cur.rowcount
                for chute in chutes:
                    cid = int(chute["id"])
                    n = int(counts.get(cid, 0))
                    note = f"全局 {body.键}={cap}（{chute['code']} 在口 {n}）"
                    # 与单口门改容量一样留 delta=0 快照，不能只改 chutes.soft_cap。
                    record_ledger(
                        conn,
                        chute_id=cid,
                        scan_id=None,
                        delta=0,
                        kind="软容量调整",
                        count_before=n,
                        cap=cap,
                        note=note,
                    )
                    audit(conn, "口门", cid, "软容量设置", note)
                audit(conn, "设置", None, "软容量设置", f"全局 {body.键}={cap} 同步口门 {synced} 个")

        return {"键": body.键, "值": value, "同步口门数": synced, "软容量策略": POLICY}

def _surface_router_settings() -> dict:
    return {"panel": "settings", "ready": True}

