"""Monthly `PeriodSummary` construction (see models.PeriodSummary / docs/API.md).

Everything here is a pure function over normalized transactions + closed lots.
Lots are attributed to the period of their **close_ts** (that is when the P&L
is realized); cash items are attributed to the period of the transaction `ts`.
"""
from __future__ import annotations

import calendar
import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Sequence

from app.analytics.ledger import TRADE_TYPES

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
TRADE_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")


# ---------------------------------------------------------------- small utils

def period_of(ts: str) -> str:
    return (ts or "")[:7]


def month_bounds(period: str) -> tuple[str, str]:
    try:
        year, month = int(period[:4]), int(period[5:7])
    except (ValueError, IndexError):
        return period, period
    last = calendar.monthrange(year, month)[1]
    return f"{period}-01", f"{period}-{last:02d}"


def _weekday_of(ts: str) -> str:
    try:
        return WEEKDAYS[datetime.fromisoformat((ts or "")[:19]).weekday()]
    except (ValueError, IndexError):
        return "Unknown"


def _f(x: Any) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _mean(values: Sequence[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def is_trade(t: dict[str, Any]) -> bool:
    """A buy/sell of a tradeable instrument (expirations do not count as trades)."""
    return (str(t.get("asset_type") or "") in TRADE_TYPES
            and str(t.get("side") or "none").lower() in ("buy", "sell"))


# ---------------------------------------------------------------- periods

def list_periods(txns: Iterable[dict[str, Any]]) -> list[str]:
    """Every YYYY-MM that has at least one transaction, ascending."""
    return sorted({period_of(str(t.get("ts") or "")) for t in txns if t.get("ts")} - {""})


def _drivers(rows: Sequence[tuple[str, float, int, float, float]]) -> list[dict[str, Any]]:
    """rows = (key, realized_pnl, trades, fees, gross) -> Driver dicts with share."""
    total_gross = sum(r[4] for r in rows)
    out = []
    for key, pnl, trades, fees, gross in rows:
        out.append({
            "key": key,
            "realized_pnl": round(pnl, 2),
            "trades": int(trades),
            "fees": round(fees, 2),
            "gross": round(gross, 2),
            "share": round(gross / total_gross, 4) if total_gross else 0.0,
        })
    out.sort(key=lambda d: (-abs(d["realized_pnl"]), -d["gross"], d["key"]))
    return out


def _dimension(lots: Sequence[dict], txns: Sequence[dict], lot_key, txn_key) -> list[dict[str, Any]]:
    """Aggregate P&L (from lots) and activity (from txns) on one dimension."""
    pnl: dict[str, float] = defaultdict(float)
    trades: dict[str, int] = defaultdict(int)
    fees: dict[str, float] = defaultdict(float)
    gross: dict[str, float] = defaultdict(float)

    for lot in lots:
        k = lot_key(lot)
        if k is None:
            continue
        pnl[k] += _f(lot.get("realized_pnl"))
    for t in txns:
        if not is_trade(t):
            continue
        k = txn_key(t)
        if k is None:
            continue
        trades[k] += 1
        fees[k] += abs(_f(t.get("fees")))
        gross[k] += abs(_f(t.get("amount")))
    keys = set(pnl) | set(trades) | set(gross)
    return _drivers([(k, pnl[k], trades[k], fees[k], gross[k]) for k in keys])


def _weekday_dimension(lots: Sequence[dict]) -> list[dict[str, Any]]:
    """Weekday is a property of when the position was OPENED, so it comes from lots."""
    pnl: dict[str, float] = defaultdict(float)
    n: dict[str, int] = defaultdict(int)
    fees: dict[str, float] = defaultdict(float)
    gross: dict[str, float] = defaultdict(float)
    for lot in lots:
        k = _weekday_of(str(lot.get("open_ts") or ""))
        if k not in TRADE_WEEKDAYS:
            if k == "Unknown":
                continue
        pnl[k] += _f(lot.get("realized_pnl"))
        n[k] += 1
        fees[k] += _f(lot.get("fees"))
        gross[k] += abs(_f(lot.get("cost"))) + abs(_f(lot.get("proceeds")))
    rows = [(k, pnl[k], n[k], fees[k], gross[k]) for k in pnl]
    out = _drivers(rows)
    order = {d: i for i, d in enumerate(WEEKDAYS)}
    out.sort(key=lambda d: order.get(d["key"], 99))
    return out


def _snapshot_for(period: str, snapshots: Sequence[dict] | None) -> dict[str, Any] | None:
    if not snapshots:
        return None
    rows = [s for s in snapshots if period_of(str(s.get("as_of") or "")) == period]
    if not rows:
        return None
    return sorted(rows, key=lambda s: str(s.get("as_of") or ""))[-1]


# ---------------------------------------------------------------- summary

def summarize_period(period: str, txns: Sequence[dict[str, Any]], lots: Sequence[dict[str, Any]],
                     snapshots: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build one PeriodSummary dict for `period` (YYYY-MM)."""
    p_txns = [t for t in txns if period_of(str(t.get("ts") or "")) == period]
    p_lots = [l for l in lots if period_of(str(l.get("close_ts") or "")) == period]
    start, end = month_bounds(period)

    trades = [t for t in p_txns if is_trade(t)]
    buys = [t for t in trades if str(t.get("side")).lower() == "buy"]
    sells = [t for t in trades if str(t.get("side")).lower() == "sell"]

    realized_pnl = sum(_f(l.get("realized_pnl")) for l in p_lots)
    fees = (sum(abs(_f(t.get("fees"))) for t in p_txns)
            + sum(abs(_f(t.get("amount"))) for t in p_txns if t.get("asset_type") == "fee"))
    dividends = sum(_f(t.get("amount")) for t in p_txns if t.get("asset_type") == "dividend")
    interest = sum(_f(t.get("amount")) for t in p_txns if t.get("asset_type") == "interest")
    net_deposits = sum(_f(t.get("amount")) for t in p_txns if t.get("asset_type") == "transfer")
    net_cash_flow = sum(_f(t.get("amount")) for t in p_txns)

    pnls = [_f(l.get("realized_pnl")) for l in p_lots]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    closed_lots = len(p_lots)
    win_rate = round(len(wins) / closed_lots, 4) if closed_lots else 0.0
    loss_rate = round(len(losses) / closed_lots, 4) if closed_lots else 0.0
    avg_win = _mean(wins)
    avg_loss = _mean(losses)
    expectancy = round(win_rate * avg_win + loss_rate * avg_loss, 2)
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 3)
    else:
        profit_factor = round(gross_profit, 3) if gross_profit else 0.0

    gross_bought = sum(abs(_f(t.get("amount"))) for t in buys)
    gross_sold = sum(abs(_f(t.get("amount"))) for t in sells)

    option_trades = [t for t in trades if t.get("asset_type") == "option"]
    options_share = round(len(option_trades) / len(trades), 4) if trades else 0.0

    by_und_bought: dict[str, float] = defaultdict(float)
    for t in buys:
        by_und_bought[str(t.get("underlying") or t.get("symbol") or "?").upper()] += abs(_f(t.get("amount")))
    top_symbol, top_share = "", 0.0
    if by_und_bought and gross_bought > 0:
        top_symbol = max(by_und_bought, key=lambda k: by_und_bought[k])
        top_share = round(by_und_bought[top_symbol] / gross_bought, 4)

    ranked = sorted(p_lots, key=lambda l: _f(l.get("realized_pnl")))
    largest_losses = [l for l in ranked if _f(l.get("realized_pnl")) < 0][:5]
    largest_wins = [l for l in reversed(ranked) if _f(l.get("realized_pnl")) > 0][:5]

    snap = _snapshot_for(period, snapshots)

    return {
        "period": period,
        "start": start,
        "end": end,
        "realized_pnl": round(realized_pnl, 2),
        "fees": round(fees, 2),
        "dividends": round(dividends, 2),
        "interest": round(interest, 2),
        "net_deposits": round(net_deposits, 2),
        "net_cash_flow": round(net_cash_flow, 2),
        "trade_count": len(trades),
        "closed_lots": closed_lots,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": expectancy,
        "profit_factor": profit_factor,
        "gross_bought": round(gross_bought, 2),
        "gross_sold": round(gross_sold, 2),
        "turnover": round(gross_bought + gross_sold, 2),
        "avg_hold_days": round(_mean([_f(l.get("hold_days")) for l in p_lots]), 2),
        "options_share": options_share,
        "concentration_top_symbol": top_symbol,
        "concentration_top_share": top_share,
        "by_underlying": _dimension(
            p_lots, p_txns,
            lambda l: str(l.get("underlying") or "?").upper(),
            lambda t: str(t.get("underlying") or t.get("symbol") or "?").upper()),
        "by_asset_type": _dimension(
            p_lots, p_txns,
            lambda l: str(l.get("asset_type") or "other"),
            lambda t: str(t.get("asset_type") or "other")),
        "by_strategy": _dimension(
            p_lots, p_txns,
            lambda l: str(l.get("strategy_tag") or "") or "untagged",
            lambda t: str(t.get("strategy_tag") or "") or "untagged"),
        "by_weekday": _weekday_dimension(p_lots),
        "largest_wins": largest_wins,
        "largest_losses": largest_losses,
        "ending_equity": _f(snap.get("equity")) if snap and snap.get("equity") is not None else None,
        "ending_cash": _f(snap.get("cash")) if snap and snap.get("cash") is not None else None,
    }


def all_period_summaries(txns: Sequence[dict[str, Any]], lots: Sequence[dict[str, Any]],
                         snapshots: Sequence[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """One PeriodSummary per month present in `txns`, ascending."""
    return [summarize_period(p, txns, lots, snapshots) for p in list_periods(txns)]


def lots_in_period(lots: Sequence[dict[str, Any]], period: str | None) -> list[dict[str, Any]]:
    if not period:
        return list(lots)
    return [l for l in lots if period_of(str(l.get("close_ts") or "")) == period]
