"""Current holdings, derived by replaying the tape into still-open FIFO lots.

If the newest snapshot carries `positions_json` (live broker pull), its
market_value / unrealized_pnl are merged in on top of the replayed cost basis.
"""
from __future__ import annotations

import json
from typing import Any, Sequence

from app.analytics.ledger import multiplier_for, open_lots


def _latest_snapshot(snapshots: Sequence[dict[str, Any]] | None) -> dict[str, Any] | None:
    rows = [s for s in (snapshots or []) if s.get("as_of")]
    return sorted(rows, key=lambda s: str(s["as_of"]))[-1] if rows else None


def _snapshot_positions(snap: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Index a snapshot's positions_json by symbol (best effort, never raises)."""
    if not snap:
        return {}
    raw = snap.get("positions_json") or "[]"
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if not isinstance(raw, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for p in raw:
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol") or p.get("contractDesc") or p.get("ticker") or "").strip()
        if sym:
            out[sym] = p
    return out


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def current_positions(txns: list[dict[str, Any]],
                      snapshots: Sequence[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """models.Position dicts for everything still held (long or short)."""
    book = open_lots(txns)
    snap = _latest_snapshot(snapshots)
    snap_pos = _snapshot_positions(snap)

    out: list[dict[str, Any]] = []
    for symbol, lots in book.items():
        qty = sum(l["dir"] * l["qty"] for l in lots)
        if abs(qty) < 1e-9:
            continue
        asset_type = lots[0].get("asset_type") or "stock"
        mult = multiplier_for(asset_type)
        total_units = sum(l["qty"] for l in lots) or 1.0
        # unit_dollars is already multiplier-inclusive; avg_cost is a per-unit price
        avg_cost = sum(l["unit_dollars"] * l["qty"] for l in lots) / total_units / (mult or 1.0)

        pos = {
            "symbol": symbol,
            "underlying": str(lots[0].get("underlying") or symbol.split(" ")[0]).upper(),
            "asset_type": asset_type,
            "qty": round(qty, 6),
            "avg_cost": round(avg_cost, 4),
            "market_value": None,
            "unrealized_pnl": None,
            "account_id": str(lots[-1].get("account_id") or ""),
            "source": str(lots[-1].get("source") or ""),
        }

        live = snap_pos.get(symbol)
        if live:
            mv = _num(live.get("market_value")) or _num(live.get("mktValue"))
            up = _num(live.get("unrealized_pnl")) or _num(live.get("unrealizedPnl"))
            last = _num(live.get("last")) or _num(live.get("mktPrice"))
            if mv is None and last is not None:
                mv = last * qty * mult
            if mv is not None:
                pos["market_value"] = round(mv, 2)
                if up is None:
                    up = mv - (avg_cost * qty * mult)
            if up is not None:
                pos["unrealized_pnl"] = round(up, 2)

        out.append(pos)

    out.sort(key=lambda p: (p["asset_type"], -abs((p["market_value"] or 0) or p["avg_cost"] * p["qty"]),
                            p["symbol"]))
    return out
