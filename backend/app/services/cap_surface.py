"""Compatibility shim for the old capacity-surface side channel.

The hard-reject architecture has one source of truth:
``app.services.capacity.chute_view`` and ``chute_capacity_ledger``.  This module
must not repaint responses or write silent ``surface_ok`` markers, so its helpers
are now transparent and side-effect free.
"""
from __future__ import annotations

from typing import Any


def overlay_overview(base: dict | None, conn=None) -> dict:
    return dict(base or {})


def after_route(out: Any, *args, **kwargs) -> Any:
    return out
