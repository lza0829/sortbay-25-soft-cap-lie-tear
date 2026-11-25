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
    at_line = n == cap
    gate_closed = n >= cap
    full = gate_closed
    state = capacity_state(n, cap)
    level = pressure_level(n, cap)
    ratio = round(n / cap, 4) if cap > 0 else None
    percent = round(100.0 * n / cap, 1) if cap > 0 else None
    code = d.get("code", str(cid))
    msg = alarm_text(code, n, cap)
    line_msg = alarm_text(code, n, cap) if gate_closed else ""
    stamp = {
        "口径": POLICY,
        "策略": POLICY,
        "口门编号": cid,
        "口门码": code,
        "在口件数": n,
        "软容量": cap,
        "count": n,
        "cap": cap,
        "贴线": at_line,
        "达到贴线阈值": gate_closed,
        "超容": over,
        "over_cap": 1 if over else 0,
        "stamp": True,
        "戳": "超容戳" if over else ("贴线戳" if gate_closed else "容量戳"),
    }
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
            "over_cap": 1 if over else 0,
            "贴线": at_line,
            "恰好贴线": at_line,
            "达到贴线阈值": gate_closed,
            "闸线闭合": gate_closed,
            "顶满": full,
            "红条顶满": full,
            "已满": at_line,
            "容量状态": state,
            "压力等级": level,
            "容量压力": level,
            # 既有机器口径里“告警/超容”保留为严格超容；贴线由独立字段表达，
            # 避免把 n==cap 与 n>cap 两种状态混成一个布尔值。
            "告警": over,
            "是否告警": over,
            "有告警": over,
            "告警标记": 1 if over else 0,
            "贴线告警": at_line,
            "闸线告警": gate_closed,
            "超容告警": over,
            "告警级别": level,
            "告警信息": msg if over else "",
            "告警内容": msg if over else "",
            "容量提示": msg,
            "容量戳": stamp,
            "贴线戳": {**stamp, "stamp": gate_closed, "戳": "贴线戳"} if gate_closed else None,
            "闸线戳": {**stamp, "stamp": gate_closed, "戳": "贴线戳"} if gate_closed else None,
            "严格超容戳": {**stamp, "stamp": over, "戳": "超容戳"} if over else None,
            "超容戳": {**stamp, "stamp": over, "戳": "超容戳"} if over else None,
            "告警列表": [
                {
                    "类型": "超容" if over else ("贴线" if gate_closed else level),
                    "级别": level,
                    "信息": msg,
                    "在口件数": n,
                    "软容量": cap,
                    "over_cap": 1 if over else 0,
                }
            ]
            if msg
            else [],
            "软容量策略": POLICY,
            "容量策略": POLICY,
            "策略": POLICY,
            "软容量口径": POLICY_CLAUSE,
            "口径条款": POLICY_CLAUSE,
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

    # 不仅核对 SUM(delta)，还按流水顺序核对 count_before/count_after 链。
    # 半截事务若留下残行，链式重放会立即断裂；正常 IMMEDIATE 事务不会。
    chain_ok = True
    chain_anomalies: list[dict[str, Any]] = []
    running = 0
    previous_after = 0
    for index, e in enumerate(events):
        before = int(e["count_before"] or 0)
        delta = int(e["delta"] or 0)
        after = int(e["count_after"] or 0)
        if before + delta != after:
            chain_ok = False
            chain_anomalies.append({
                "流水编号": e["id"],
                "序号": index,
                "类型": e["kind"],
                "异常码": "DELTA_CHAIN_BROKEN",
                "问题": f"count_before({before}) + delta({delta}) != count_after({after})",
            })
        if before != previous_after:
            chain_ok = False
            chain_anomalies.append({
                "流水编号": e["id"],
                "序号": index,
                "类型": e["kind"],
                "异常码": "BEFORE_AFTER_CHAIN_BROKEN",
                "问题": f"count_before({before}) != 上一条 count_after({previous_after})",
            })
        running += delta
        # 即使某行自相矛盾，也以落库 after 作为下一行预期，尽量一次暴露断链位置。
        previous_after = after
    if chain_ok and running != ledger_sum:
        chain_ok = False
        chain_anomalies.append({
            "异常码": "RUNNING_SUM_BROKEN",
            "问题": f"链式累计 {running} != SUM(delta) {ledger_sum}",
        })

    scan_ids = [int(e["scan_id"]) for e in events if e.get("scan_id") is not None]
    bag_rows = conn.execute("SELECT id, code FROM bags WHERE chute_id=?", (chute_id,)).fetchall()
    bag_ids = [int(r["id"]) for r in bag_rows]
    bag_code_to_id = {str(r["code"]): int(r["id"]) for r in bag_rows}
    batch_ids = [
        int(r["divert_batch_id"])
        for r in conn.execute(
            """
            SELECT DISTINCT divert_batch_id
            FROM scans
            WHERE chute_id=? AND divert_batch_id IS NOT NULL
            """,
            (chute_id,),
        ).fetchall()
    ]

    audit_events: list[dict] = []
    where = ["(a.kind='口门' AND a.ref_id=?)", "(a.note LIKE ?)"]
    params: list[Any] = [chute_id, f"%{chute['code']}%"]
    if scan_ids:
        marks = ",".join("?" for _ in scan_ids)
        where.append(f"(a.kind='扫描' AND a.ref_id IN ({marks}))")
        params.extend(scan_ids)
        where.append(f"(a.note IN ({marks}))")
        params.extend([str(x) for x in scan_ids])
    if bag_ids:
        marks = ",".join("?" for _ in bag_ids)
        where.append(f"(a.kind='封袋' AND a.ref_id IN ({marks}))")
        params.extend(bag_ids)
    if batch_ids:
        marks = ",".join("?" for _ in batch_ids)
        where.append(f"(a.kind='回流' AND a.ref_id IN ({marks}))")
        params.extend(batch_ids)
    audit_events = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT DISTINCT a.* FROM audit_events a
            WHERE {' OR '.join(where)}
            ORDER BY a.id ASC
            """,
            params,
        ).fetchall()
    ]
    audit_keys = {
        (int(a["ref_id"]), str(a["action"]))
        for a in audit_events
        if a.get("ref_id") is not None
    }
    audit_notes = {str(a.get("note") or "") for a in audit_events}
    route_event_keys = {
        (int(r["scan_id"]), str(r["action"]))
        for r in conn.execute(
            "SELECT scan_id, action FROM route_events WHERE chute_id=?",
            (chute_id,),
        ).fetchall()
    }

    # 逐条核对裁决、账本、阈值戳、route_event 与裁决审计是否同命运。
    # SUM 只能抓“数量差”；这里还能抓住“应拒却写了成功流水”“有账无审计”
    # “戳被静默抹掉/伪造”等撕裂。
    fate_anomalies: list[dict[str, Any]] = []

    def add_fate_anomaly(event: dict, code: str, problem: str) -> None:
        fate_anomalies.append({
            "流水编号": event.get("id"),
            "扫描编号": event.get("scan_id"),
            "类型": event.get("kind"),
            "异常码": code,
            "问题": problem,
        })

    for e in events:
        kind = str(e.get("kind") or "")
        sid = e.get("scan_id")
        before = int(e["count_before"] or 0)
        after = int(e["count_after"] or 0)
        delta = int(e["delta"] or 0)
        cap_snapshot = e.get("cap_snapshot")
        cap_value = int(cap_snapshot) if cap_snapshot is not None else None

        if cap_value is not None:
            expected_over = 1 if after > cap_value else 0
            if int(e.get("over_cap") or 0) != expected_over:
                add_fate_anomaly(
                    e,
                    "OVER_CAP_STAMP_TORN",
                    f"超容戳={int(e.get('over_cap') or 0)}，按 after={after}/cap={cap_value} 应为 {expected_over}",
                )

        if kind == "路由落入":
            if sid is None:
                add_fate_anomaly(e, "ROUTE_LEDGER_WITHOUT_SCAN", "路由落入流水缺少 scan_id")
            if delta != 1:
                add_fate_anomaly(e, "ROUTE_DELTA_INVALID", f"路由落入 delta 应为 1，实际 {delta}")
            if cap_value is None:
                add_fate_anomaly(e, "CAP_SNAPSHOT_MISSING", "路由落入流水缺少软容量快照")
            elif before >= cap_value:
                add_fate_anomaly(
                    e,
                    "SHOULD_REJECT_BUT_COUNTED",
                    f"应硬拒：count_before={before} >= cap={cap_value}，却写入路由落入",
                )
            if sid is not None and (int(sid), "路由裁决") not in audit_keys:
                add_fate_anomaly(e, "ROUTE_AUDIT_MISSING", "路由落入缺少同笔 路由裁决 审计")
            if sid is not None and (int(sid), "路由落入") not in route_event_keys:
                add_fate_anomaly(e, "ROUTE_EVENT_MISSING", "路由落入缺少 route_events 落入事件")

        elif kind == "裁决硬拒":
            if sid is None:
                add_fate_anomaly(e, "REJECT_LEDGER_WITHOUT_SCAN", "裁决硬拒流水缺少 scan_id")
            if delta != 0:
                add_fate_anomaly(e, "REJECT_DELTA_INVALID", f"裁决硬拒 delta 应为 0，实际 {delta}")
            if cap_value is None:
                add_fate_anomaly(e, "CAP_SNAPSHOT_MISSING", "裁决硬拒流水缺少软容量快照")
            elif before < cap_value:
                add_fate_anomaly(
                    e,
                    "SHOULD_ALLOW_BUT_REJECTED",
                    f"应放行：count_before={before} < cap={cap_value}，却写入硬拒",
                )
            if sid is not None and (int(sid), "软容量硬拒") not in audit_keys:
                add_fate_anomaly(e, "REJECT_AUDIT_MISSING", "裁决硬拒缺少同笔 软容量硬拒 审计")

        elif kind == "错分回流":
            if sid is None:
                add_fate_anomaly(e, "DIVERT_LEDGER_WITHOUT_SCAN", "错分回流流水缺少 scan_id")
            if delta != -1:
                add_fate_anomaly(e, "DIVERT_DELTA_INVALID", f"错分回流 delta 应为 -1，实际 {delta}")
            if sid is not None and (int(sid), "错分回流") not in route_event_keys:
                add_fate_anomaly(e, "DIVERT_EVENT_MISSING", "错分回流缺少 route_events 回流事件")
            if sid is not None and str(sid) not in audit_notes:
                add_fate_anomaly(e, "DIVERT_AUDIT_MISSING", "错分回流缺少同笔回流批次审计")

        elif kind == "封袋移出":
            if delta > 0:
                add_fate_anomaly(e, "BAG_SEAL_DELTA_INVALID", f"封袋移出 delta 不应为正，实际 {delta}")
            bag_id = next(
                (bid for code, bid in bag_code_to_id.items() if code in str(e.get("note") or "")),
                None,
            )
            if bag_id is None:
                add_fate_anomaly(e, "BAG_SEAL_LINK_MISSING", "封袋移出流水未能关联到本口门袋号")
            elif (bag_id, "封口") not in audit_keys:
                add_fate_anomaly(e, "BAG_SEAL_AUDIT_MISSING", "封袋移出缺少同袋封口审计")

    cap_adjust_ledger_n = sum(1 for e in events if e.get("kind") == "软容量调整")
    cap_adjust_audit_n = sum(
        1
        for a in audit_events
        if a.get("kind") == "口门"
        and a.get("ref_id") == chute_id
        and a.get("action") == "软容量设置"
    )
    if cap_adjust_audit_n < cap_adjust_ledger_n:
        fate_anomalies.append({
            "异常码": "CAP_SETTING_AUDIT_MISSING",
            "问题": (
                f"软容量调整流水 {cap_adjust_ledger_n} 条，"
                f"口门软容量设置审计仅 {cap_adjust_audit_n} 条"
            ),
        })

    over_stamps = [e for e in events if int(e.get("over_cap") or 0) == 1]
    sum_ok = ledger_sum == live
    fate_ok = not fate_anomalies
    all_ok = sum_ok and chain_ok and fate_ok
    return {
        "口门编号": chute_id,
        "口门码": chute["code"],
        "软容量": view["软容量"],
        "在口件数": live,
        "在口数": live,
        "账本累计": ledger_sum,
        "重放件数": ledger_sum,
        "重放终值": running,
        "流水笔数": int(agg["n"]),
        "一致": sum_ok,
        "sum_ok": sum_ok,
        "可重放": all_ok,
        "replay_ok": all_ok,
        "链式一致": chain_ok,
        "chain_ok": chain_ok,
        "链式异常": chain_anomalies,
        "命运一致": fate_ok,
        "fate_ok": fate_ok,
        "裁决同命运一致": fate_ok,
        "命运异常": fate_anomalies,
        "全部一致": all_ok,
        "同命运一致": all_ok,
        "超容": view["超容"],
        "贴线": view["贴线"],
        "达到贴线阈值": view["达到贴线阈值"],
        "红条顶满": view["红条顶满"],
        "容量状态": view["容量状态"],
        "告警信息": view["告警信息"],
        "超容戳": over_stamps[-1] if over_stamps else None,
        "超容戳记录": over_stamps,
        "审计事件": audit_events,
        "裁决审计": audit_events,
        "软容量策略": POLICY,
        "策略": POLICY,
        "口径": POLICY_CLAUSE,
        "口径条款": POLICY_CLAUSE,
        "流水": events,
        "口门": view,
    }


def replay_all(conn) -> dict:
    items = []
    all_ok = True
    views = all_chute_views(conn)
    for v in views:
        rp = replay_chute(conn, v["id"])
        all_ok = all_ok and bool(rp["全部一致"])
        items.append(
            {
                "口门编号": v["id"],
                "口门码": v["code"],
                "软容量": v["软容量"],
                "在口件数": v["在口件数"],
                "账本累计": rp["账本累计"],
                "一致": rp["一致"],
                "链式一致": rp["链式一致"],
                "命运一致": rp["命运一致"],
                "全部一致": rp["全部一致"],
                "同命运一致": rp["同命运一致"],
                "命运异常数": len(rp["命运异常"]),
                "超容": v["超容"],
                "贴线": v["贴线"],
                "容量状态": v["容量状态"],
            }
        )
    over = [v for v in views if v["超容"]]
    line = [v for v in views if v["达到贴线阈值"]]
    return {
        "全部一致": all_ok,
        "可重放": all_ok,
        "同命运一致": all_ok,
        "软容量策略": POLICY,
        "策略": POLICY,
        "口径": POLICY_CLAUSE,
        "口径条款": POLICY_CLAUSE,
        "口门": items,
        "异常口门": [x for x in items if not x["全部一致"]],
        "超容口门": over,
        "闸线闭闭口门": line,
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
        "容量账一致": chute_sum == int(status_n),
        "可重放": chute_sum == int(status_n),
        "超容口门数": len(over),
        "贴线口门数": len([v for v in views if v["贴线"]]),
        "超容口门": over,
        "贴线口门": [v for v in views if v["贴线"]],
        "软容量策略": POLICY,
        "策略": POLICY,
        "口径条款": POLICY_CLAUSE,
    }

