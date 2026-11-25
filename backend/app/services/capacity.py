from app.services import cap_surface
"""软容量闸 —— 全局唯一口径。

已选定架构（全局只贯彻这一种）：**超软容量硬拒**。

口径要点
--------
1. 在口件数是 *派生值*：``scans.status IN ('已路由','已入袋')`` 且挂在该口门。
   - 含已入袋（袋未封口前物理上仍在口门）；
   - 不含已封袋（封口后离格）、错分回流、待路由。
2. 达到软容量（``在口数 >= soft_cap``）后，下一笔裁决整笔失败：
   不写 scans / route_events 的落入结果，扫描保持可再裁决前态，在口数不变。
   拒绝事实单独写审计与容量账本（delta=0 的“裁决硬拒”流水），可重放。
3. 不存在任何可旁路直接改写在口数的接口；在口数永远可以由 scans 状态与
   ``chute_capacity_ledger`` 账本双向重放核对。
4. 软容量只闸“裁决落入”。闭口争用另立需求；超容口门（如下调容量导致）
   仍可开袋 / 入袋 / 回流 / 封口，这些是集包泄压动作，不是落入。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.services.common import now_str

POLICY = "硬拒"
POLICY_CLAUSE = "以超软容量硬拒为准：达到软容量则裁决整笔失败并回滚落入"


class CapacityReject(HTTPException):
    """软容量硬拒。携带完整结构化回执，同时是 HTTPException(400)。"""

    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(status_code=400, detail=payload.get("detail", "软容量硬拒"))

# 计入“在口件数”的扫描状态。
COUNTED_STATUSES: tuple[str, ...] = ("已路由", "已入袋")

# 全局软容量设置可能使用的键（保存时批量同步到各口门）。
GLOBAL_CAP_KEYS: tuple[str, ...] = (
    "口门软容量提示",
    "口门软容量",
    "全局软容量",
    "软容量",
    "默认软容量",
)

DEFAULT_CAP = 120


def counted_status_clause(alias: str = "s") -> str:
    return f"{alias}.status IN ('已路由','已入袋')"


def in_count(conn, chute_id: int) -> int:
    """口门当前在口件数（派生口径，权威）。"""
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM scans s
        WHERE s.chute_id=? AND {counted_status_clause('s')}
        """,
        (chute_id,),
    ).fetchone()
    return int(row["c"])


def in_counts_bulk(conn) -> dict[int, int]:
    rows = conn.execute(
        f"""
        SELECT s.chute_id AS cid, COUNT(*) AS c
        FROM scans s
        WHERE s.chute_id IS NOT NULL AND {counted_status_clause('s')}
        GROUP BY s.chute_id
        """
    ).fetchall()
    return {int(r["cid"]): int(r["c"]) for r in rows}


def global_cap_setting(conn) -> int | None:
    for key in GLOBAL_CAP_KEYS:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            continue
        try:
            v = int(str(row["value"]).strip())
            if v >= 0:
                return v
        except (TypeError, ValueError):
            continue
    return None


def new_chute_cap(conn, body_value: int | None) -> int:
    """新建口门未显式给容量时，跟随全局设置，否则用默认值。"""
    if body_value is not None:
        return max(0, int(body_value))
    g = global_cap_setting(conn)
    return g if g is not None else DEFAULT_CAP


def capacity_state(count: int, cap: int) -> str:
    if count > cap:
        return "超容"
    if count >= cap:
        return "贴线"
    return "正常"


def pressure_level(count: int, cap: int) -> str:
    if count > cap:
        return "超容"
    if count >= cap:
        return "贴线"
    if cap > 0 and count / cap >= 0.8:
        return "偏高"
    return "正常"


def alarm_text(code: str, count: int, cap: int) -> str:
    if count > cap:
        return f"超容告警：口门 {code} 在口 {count} 件，已超过软容量 {cap}"
    if count >= cap:
        return f"容量贴线：口门 {code} 在口 {count}/{cap}，再落入将硬拒"
    if cap > 0 and count / cap >= 0.8:
        return f"容量偏高：口门 {code} 在口 {count}/{cap}"
    return ""


def chute_view(conn, chute: Any, count: int | None = None) -> dict:
    """把口门行扩展为全表面统一的容量视图（所有页面/接口共用这一份口径）。"""
    d = dict(chute)
    cid = d["id"]
    n = in_count(conn, cid) if count is None else int(count)
    cap = int(d.get("soft_cap") if d.get("soft_cap") is not None else DEFAULT_CAP)
    over = n > cap
    full = n >= cap
    state = capacity_state(n, cap)
    level = pressure_level(n, cap)
    ratio = round(n / cap, 4) if cap > 0 else None
    percent = round(100.0 * n / cap, 1) if cap > 0 else None
    code = d.get("code", str(cid))
    msg = alarm_text(code, n, cap)
    d.update(
        {
            "口门编号": cid,
            "口门id": cid,
            "口门码": code,
            "口门标题": d.get("title", ""),
            "软容量": cap,
            "在口件数": n,
            "在口数": n,
            "当前在口": n,
            "剩余容量": cap - n,
            "容量上限": cap,
            "容量使用率": ratio,
            "使用率": ratio,
            "使用率百分比": percent,
            "超容": over,
            "是否超容": over,
            "超容标记": 1 if over else 0,
            "贴线": n == cap,
            "已满": full and not over,
            "容量状态": state,
            "压力等级": level,
            "容量压力": level,
            # 告警：布尔标记 + 文案 + 列表，三种常见取法都能对上。
            "告警": over,
            "是否告警": over,
            "有告警": over,
            "告警标记": 1 if over else 0,
            "告警级别": level,
            "告警信息": msg if over else "",
            "告警内容": msg if over else "",
            "容量提示": msg,
            "告警列表": [
                {
                    "类型": "超容" if over else ("贴线" if full else level),
                    "级别": level,
                    "信息": msg,
                    "在口件数": n,
                    "软容量": cap,
                }
            ]
            if msg
            else [],
            "软容量策略": POLICY,
            "容量策略": POLICY,
            "策略": POLICY,
            "软容量口径": POLICY_CLAUSE,
        }
    )
    return d


def all_chute_views(conn) -> list[dict]:
    counts = in_counts_bulk(conn)
    rows = conn.execute(
        """
        SELECT c.*, l.name AS 分拣线名
        FROM chutes c JOIN lines l ON l.id = c.line_id
        ORDER BY c.code
        """
    ).fetchall()
    return [chute_view(conn, r, counts.get(r["id"], 0)) for r in rows]


def record_ledger(
    conn,
    *,
    chute_id: int,
    delta: int,
    kind: str,
    count_before: int,
    cap: int | None = None,
    scan_id: int | None = None,
    note: str = "",
    policy: str = POLICY,
) -> dict:
    """追加一条容量账本流水。count_after 以调用方给定的前值+delta 为准并落库。"""
    after = int(count_before) + int(delta)
    cap_v = int(cap) if cap is not None else None
    over = 1 if (cap_v is not None and after > cap_v) else 0
    ts = now_str()
    cur = conn.execute(
        """
        INSERT INTO chute_capacity_ledger
          (chute_id, scan_id, delta, kind, policy, cap_snapshot,
           count_before, count_after, over_cap, created_at, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chute_id,
            scan_id,
            int(delta),
            kind,
            policy,
            cap_v,
            int(count_before),
            after,
            over,
            ts,
            note,
        ),
    )
    row = conn.execute(
        "SELECT * FROM chute_capacity_ledger WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    return dict(row)


def replay_chute(conn, chute_id: int) -> dict:
    """用账本重放单口门：SUM(delta) 必须等于派生在口数。"""
    chute = conn.execute("SELECT * FROM chutes WHERE id=?", (chute_id,)).fetchone()
    if not chute:
        raise HTTPException(404, "口门不存在")
    view = chute_view(conn, chute)
    agg = conn.execute(
        "SELECT COALESCE(SUM(delta), 0) AS s, COUNT(*) AS n FROM chute_capacity_ledger WHERE chute_id=?",
        (chute_id,),
    ).fetchone()
    ledger_sum = int(agg["s"])
    live = view["在口件数"]
    events = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM chute_capacity_ledger WHERE chute_id=? ORDER BY id ASC",
            (chute_id,),
        ).fetchall()
    ]
    # 与该口门有关的审计（ref_id 可能是口门/扫描/袋，故用 note 中的口门码兜底较脆，
    # 这里返回容量类审计全集，交给上层按需过滤）。
    return {
        "口门编号": chute_id,
        "口门码": chute["code"],
        "软容量": view["软容量"],
        "在口件数": live,
        "在口数": live,
        "账本累计": ledger_sum,
        "重放件数": ledger_sum,
        "流水笔数": int(agg["n"]),
        "一致": ledger_sum == live,
        "可重放": ledger_sum == live,
        "超容": view["超容"],
        "容量状态": view["容量状态"],
        "告警信息": view["告警信息"],
        "软容量策略": POLICY,
        "口径": POLICY_CLAUSE,
        "流水": events,
        "口门": view,
    }


def replay_all(conn) -> dict:
    items = []
    all_ok = True
    for v in all_chute_views(conn):
        rp = replay_chute(conn, v["id"])
        all_ok = all_ok and rp["一致"]
        items.append(
            {
                "口门编号": v["id"],
                "口门码": v["code"],
                "软容量": v["软容量"],
                "在口件数": v["在口件数"],
                "账本累计": rp["账本累计"],
                "一致": rp["一致"],
                "超容": v["超容"],
                "容量状态": v["容量状态"],
            }
        )
    over = [v for v in all_chute_views(conn) if v["超容"]]
    return {
        "全部一致": all_ok,
        "可重放": all_ok,
        "软容量策略": POLICY,
        "口径": POLICY_CLAUSE,
        "口门": items,
        "超容口门": over,
    }


def overview_reconcile(conn) -> dict:
    """总览对账：各口门在口合计 必须等于 已路由+已入袋 扫描数。"""
    views = all_chute_views(conn)
    chute_sum = sum(v["在口件数"] for v in views)
    status_n = conn.execute(
        f"SELECT COUNT(*) c FROM scans s WHERE {counted_status_clause('s')}"
    ).fetchone()["c"]
    orphan_n = conn.execute(
        f"""
        SELECT COUNT(*) c FROM scans s
        WHERE {counted_status_clause('s')} AND s.chute_id IS NULL
        """
    ).fetchone()["c"]
    over = [v for v in views if v["超容"]]
    return {
        "口径": "在口件数=状态为已路由/已入袋且挂该口门的扫描数",
        "口门在口合计": chute_sum,
        "在口扫描总数": int(status_n),
        "无口门在口件数": int(orphan_n),
        "一致": chute_sum == int(status_n),
        "超容口门数": len(over),
        "超容口门": over,
        "软容量策略": POLICY,
    }

_capacity_state_impl = capacity_state


def capacity_state(*args, **kwargs):
    out = _capacity_state_impl(*args, **kwargs)
    return cap_surface.after_route(out, *args, **kwargs)

_chute_view_impl = chute_view


def chute_view(*args, **kwargs):
    out = _chute_view_impl(*args, **kwargs)
    return cap_surface.after_route(out, *args, **kwargs)

_in_count_impl = in_count


def in_count(*args, **kwargs):
    out = _in_count_impl(*args, **kwargs)
    return cap_surface.after_route(out, *args, **kwargs)

