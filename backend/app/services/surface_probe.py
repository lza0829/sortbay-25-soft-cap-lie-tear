"""Optional surface probe compatibility helper.

No separate probe state is authoritative; callers should read ``/api/软容量``
or replay the capacity ledger.  ``merge_probe`` therefore leaves payloads
unchanged.
"""
from __future__ import annotations


def probe_panel(conn) -> dict:
    return {"ok": False, "enabled": False}


def merge_probe(base: dict, conn) -> dict:
    return dict(base or {})
