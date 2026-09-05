"""Analytics facade — the ONLY surface routes and agent tools should import.

Everything is read-through from sqlite with a tiny in-process cache keyed by
(transaction count, max ts) so repeated tool calls inside one agent run do not
re-run FIFO matching.
"""
from __future__ import annotations

import threading
from typing import Any, Optional

from app.analytics import ledger, periods as periods_mod, positions as positions_mod, variance
from app.db import fetch_transactions, get_conn, rows_to_dicts

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"key": None, "txns": [], "lots": [], "summaries": [], "snapshots": []}


# ---------------------------------------------------------------- loading

def load_txns() -> list[dict[str, Any]]:
    """Every transaction, ascending by ts."""
    return fetch_transactions()


def load_snapshots() -> list[dict[str, Any]]:
    try:
        with get_conn() as conn:
            return rows_to_dicts(conn.execute("SELECT * FROM snapshots ORDER BY as_of ASC").fetchall())
    except Exception:
        return []


def _fingerprint(txns: list[dict[str, Any]]) -> tuple[int, str]:
    return (len(txns), max((str(t.get("ts") or "") for t in txns), default=""))


def _state() -> dict[str, Any]:
    """Load + compute (lots, summaries) once per distinct transaction set."""
    txns = load_txns()
    key = _fingerprint(txns)
    with _LOCK:
        if _CACHE["key"] == key:
            return _CACHE
        snapshots = load_snapshots()
        lots = ledger.match_lots(txns)
        summaries = periods_mod.all_period_summaries(txns, lots, snapshots)
        _CACHE.update({"key": key, "txns": txns, "lots": lots,
                       "summaries": summaries, "snapshots": snapshots})
        return _CACHE


def invalidate_cache() -> None:
    """Call after an ingest so the next read recomputes."""
    with _LOCK:
        _CACHE["key"] = None


def has_data() -> bool:
    return bool(_state()["txns"])


# ---------------------------------------------------------------- periods

def get_periods() -> list[str]:
    """YYYY-MM strings ascending."""
    return [s["period"] for s in _state()["summaries"]]


def get_period_summaries() -> list[dict[str, Any]]:
    return list(_state()["summaries"])


def get_summary(period: str) -> Optional[dict[str, Any]]:
    for s in _state()["summaries"]:
        if s["period"] == period:
            return s
    return None


def latest_two_periods() -> Optional[tuple[str, str]]:
    """(previous, latest) — the default comparison the UI opens on."""
    ps = get_periods()
    if len(ps) < 2:
        return None
    return ps[-2], ps[-1]


def get_compare(a: str, b: str) -> Optional[dict[str, Any]]:
    """VarianceReport dict, or None when either period is unknown."""
    st = _state()
    sa, sb = get_summary(a), get_summary(b)
    if sa is None or sb is None:
        return None
    return variance.compare_periods(sa, sb, st["lots"], st["txns"])


# ---------------------------------------------------------------- rows

def get_lots(period: str | None = None, underlying: str | None = None,
             limit: int = 200) -> list[dict[str, Any]]:
    """Closed lots, newest close first, filtered to a period / underlying."""
    lots = _state()["lots"]
    if period:
        lots = [l for l in lots if str(l.get("close_ts") or "")[:7] == period]
    if underlying:
        u = underlying.upper()
        lots = [l for l in lots if str(l.get("underlying") or "").upper() == u]
    lots = sorted(lots, key=lambda l: str(l.get("close_ts") or ""), reverse=True)
    return lots[: max(int(limit or 200), 0)]


def get_transactions(period: str | None = None, symbol: str | None = None,
                     underlying: str | None = None, asset_type: str | None = None,
                     limit: int = 200) -> list[dict[str, Any]]:
    """Raw transactions, newest first, filtered."""
    rows = _state()["txns"]
    if period:
        rows = [t for t in rows if str(t.get("ts") or "")[:7] == period]
    if symbol:
        s = symbol.upper()
        rows = [t for t in rows if str(t.get("symbol") or "").upper() == s]
    if underlying:
        u = underlying.upper()
        rows = [t for t in rows if str(t.get("underlying") or "").upper() == u]
    if asset_type:
        rows = [t for t in rows if str(t.get("asset_type") or "") == asset_type]
    rows = sorted(rows, key=lambda t: str(t.get("ts") or ""), reverse=True)
    return [{k: v for k, v in t.items() if k != "raw_json"} for t in rows[: max(int(limit or 200), 0)]]


def get_positions() -> list[dict[str, Any]]:
    st = _state()
    return positions_mod.current_positions(st["txns"], st["snapshots"])


# ---------------------------------------------------------------- performance

def performance() -> dict[str, Any]:
    """Whole-book performance for GET /performance."""
    st = _state()
    summaries, lots = st["summaries"], st["lots"]

    monthly, realized_cum, deposits_cum = [], 0.0, 0.0
    for s in summaries:
        realized_cum += float(s.get("realized_pnl") or 0)
        deposits_cum += float(s.get("net_deposits") or 0)
        monthly.append({
            "period": s["period"],
            "realized_pnl": round(float(s.get("realized_pnl") or 0), 2),
            "cum_realized": round(realized_cum, 2),
            "net_deposits": round(float(s.get("net_deposits") or 0), 2),
            "cum_net_deposits": round(deposits_cum, 2),
            "fees": round(float(s.get("fees") or 0), 2),
            "trade_count": int(s.get("trade_count") or 0),
            "win_rate": float(s.get("win_rate") or 0),
        })

    # max drawdown measured on the cumulative realized-P&L curve
    peak, max_dd = float("-inf"), 0.0
    for row in monthly:
        peak = max(peak, row["cum_realized"])
        max_dd = min(max_dd, row["cum_realized"] - peak)

    pnls = [float(l.get("realized_pnl") or 0) for l in lots]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    win_rate = round(len(wins) / len(pnls), 4) if pnls else 0.0
    loss_rate = round(len(losses) / len(pnls), 4) if pnls else 0.0
    avg_win = round(sum(wins) / len(wins), 2) if wins else 0.0
    avg_loss = round(sum(losses) / len(losses), 2) if losses else 0.0
    best = max(monthly, key=lambda r: r["realized_pnl"])["period"] if monthly else None
    worst = min(monthly, key=lambda r: r["realized_pnl"])["period"] if monthly else None

    by_und: dict[str, dict[str, Any]] = {}
    for lot in lots:
        k = str(lot.get("underlying") or "?").upper()
        d = by_und.setdefault(k, {"key": k, "realized_pnl": 0.0, "trades": 0, "fees": 0.0,
                                  "gross": 0.0, "share": 0.0})
        d["realized_pnl"] += float(lot.get("realized_pnl") or 0)
        d["trades"] += 1
        d["fees"] += float(lot.get("fees") or 0)
        d["gross"] += abs(float(lot.get("cost") or 0)) + abs(float(lot.get("proceeds") or 0))
    total_gross = sum(d["gross"] for d in by_und.values())
    for d in by_und.values():
        d["realized_pnl"] = round(d["realized_pnl"], 2)
        d["fees"] = round(d["fees"], 2)
        d["gross"] = round(d["gross"], 2)
        d["share"] = round(d["gross"] / total_gross, 4) if total_gross else 0.0
    ranked = sorted(by_und.values(), key=lambda d: -abs(d["realized_pnl"]))[:15]

    return {
        "monthly": monthly,
        "stats": {
            "total_realized": round(sum(p for p in pnls), 2),
            "total_fees": round(sum(float(s.get("fees") or 0) for s in summaries), 2),
            "total_net_deposits": round(deposits_cum, 2),
            "months": len(monthly),
            "best_period": best,
            "worst_period": worst,
            "max_drawdown": round(max_dd, 2),
            "win_rate": win_rate,
            "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else round(gross_profit, 3),
            "expectancy": round(win_rate * avg_win + loss_rate * avg_loss, 2),
            "avg_hold_days": round(sum(float(l.get("hold_days") or 0) for l in lots) / len(lots), 2) if lots else 0.0,
        },
        "by_underlying": ranked,
        "positions": get_positions(),
    }


# ---------------------------------------------------------------- overview

def overview() -> dict[str, Any]:
    """The GET /portfolio/overview payload."""
    st = _state()
    txns, summaries = st["txns"], st["summaries"]
    if not txns:
        return {"has_data": False, "sources": [], "transactions": 0, "periods": [],
                "latest_period": None, "equity_curve": [], "positions": []}

    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for t in txns:
        key = (str(t.get("source") or ""), str(t.get("account_id") or ""))
        b = buckets.setdefault(key, {"source": key[0], "account_id": key[1], "count": 0,
                                     "start": None, "end": None})
        ts = str(t.get("ts") or "")[:10]
        b["count"] += 1
        if ts:
            b["start"] = ts if b["start"] is None else min(b["start"], ts)
            b["end"] = ts if b["end"] is None else max(b["end"], ts)

    curve, realized_cum, deposits_cum = [], 0.0, 0.0
    for s in summaries:
        realized_cum += float(s.get("realized_pnl") or 0)
        deposits_cum += float(s.get("net_deposits") or 0)
        curve.append({"period": s["period"],
                      "realized_cum": round(realized_cum, 2),
                      "net_deposits_cum": round(deposits_cum, 2)})

    return {
        "has_data": True,
        "sources": sorted(buckets.values(), key=lambda b: (b["source"], b["account_id"])),
        "transactions": len(txns),
        "periods": [s["period"] for s in summaries],
        "latest_period": summaries[-1]["period"] if summaries else None,
        "equity_curve": curve,
        "positions": get_positions(),
    }
