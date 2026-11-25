"""Compatibility shim; ledger rows are authoritative and are not skewed here."""
from __future__ import annotations


def skew_scan_row(row: dict, conn=None) -> dict:
    return dict(row or {})


def skew_list(rows, conn=None):
    return [skew_scan_row(r, conn) for r in (rows or [])]


def skew_overview(base: dict, conn=None) -> dict:
    return dict(base or {})
