"""FIFO lot matching.

`match_lots(txns)` walks every trade transaction in time order and produces one
`ClosedLot` per matched open/close pair (partial fills split into several lots).

Matching is per `symbol`, so stocks match per ticker, options match per full
contract symbol ("NVDA 2026-06-20 C 140") and crypto per coin symbol.

Rules
-----
* buy  -> opens a long lot (or closes short lots FIFO first)
* sell -> closes long lots FIFO (or opens a short lot when flat/short)
* option expirations / assignments (side "none", open_close "close", or a
  description containing "expir") close the open position at price 0
* dollars for a leg prefer ``abs(amount)`` (the broker's true cash flow) and
  fall back to ``price * qty * multiplier`` (100 for options)
* fees are allocated pro-rata by the quantity consumed from each leg
* realized_pnl = proceeds - cost - fees  (works for longs and shorts alike)
"""
from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Iterable

TRADE_TYPES = {"stock", "option", "crypto"}
OPTION_MULTIPLIER = 100.0


# ---------------------------------------------------------------- helpers

def multiplier_for(asset_type: str) -> float:
    return OPTION_MULTIPLIER if asset_type == "option" else 1.0


def _ts(t: dict[str, Any]) -> str:
    return str(t.get("ts") or "")


def _parse_dt(ts: str) -> datetime | None:
    s = (ts or "").strip()
    if not s:
        return None
    for cut in (s, s.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(cut)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s[:10])
    except ValueError:
        return None


def hold_days_between(open_ts: str, close_ts: str) -> float:
    a, b = _parse_dt(open_ts), _parse_dt(close_ts)
    if a is None or b is None:
        return 0.0
    return round(max((b - a).total_seconds(), 0.0) / 86400.0, 3)


def is_expiration(t: dict[str, Any]) -> bool:
    """Option expiration / assignment / exercise: closes at zero premium."""
    if t.get("asset_type") != "option":
        return False
    desc = str(t.get("description") or "").lower()
    if "expir" in desc:
        return True
    side = str(t.get("side") or "none").lower()
    return side in ("none", "") and str(t.get("open_close") or "") == "close"


def leg_dollars(t: dict[str, Any]) -> float:
    """Gross dollar value of a leg (always positive)."""
    amount = float(t.get("amount") or 0)
    if amount:
        return abs(amount)
    mult = multiplier_for(str(t.get("asset_type") or ""))
    return abs(float(t.get("price") or 0)) * abs(float(t.get("qty") or 0)) * mult


def lot_id_for(symbol: str, open_ts: str, close_ts: str, qty: float,
               open_ids: list[str], close_ids: list[str]) -> str:
    payload = f"{symbol}|{open_ts}|{close_ts}|{qty:.6f}|{','.join(open_ids)}|{','.join(close_ids)}"
    return "lot_" + hashlib.sha1(payload.encode()).hexdigest()[:10]


def trade_txns(txns: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only tradeable asset types, sorted by (ts, id)."""
    rows = [t for t in txns if str(t.get("asset_type") or "") in TRADE_TYPES]
    return sorted(rows, key=lambda t: (_ts(t), str(t.get("id") or "")))


# ---------------------------------------------------------------- core

def _signed_direction(t: dict[str, Any], open_qty_signed: float) -> int:
    """+1 = this leg adds long exposure, -1 = adds short exposure."""
    side = str(t.get("side") or "none").lower()
    if side == "buy":
        return 1
    if side == "sell":
        return -1
    # expiration/assignment: close whatever is open
    if open_qty_signed > 0:
        return -1
    if open_qty_signed < 0:
        return 1
    return -1


def replay(txns: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Walk the tape once. Returns (closed_lots, still_open_lots_by_symbol)."""
    rows = trade_txns(txns)
    books: dict[str, deque] = defaultdict(deque)
    closed: list[dict[str, Any]] = []

    for t in rows:
        symbol = str(t.get("symbol") or "").strip()
        if not symbol:
            continue
        asset_type = str(t.get("asset_type") or "")
        mult = multiplier_for(asset_type)
        book = books[symbol]

        open_signed = sum(l["dir"] * l["qty"] for l in book)
        expiry = is_expiration(t)
        direction = _signed_direction(t, open_signed)

        qty = abs(float(t.get("qty") or 0))
        if expiry and qty <= 0:
            qty = abs(open_signed)
        if qty <= 0:
            continue

        dollars = 0.0 if expiry else leg_dollars(t)
        unit_dollars = dollars / qty if qty else 0.0
        price = 0.0 if expiry else (float(t.get("price") or 0) or (unit_dollars / mult if mult else 0.0))
        fees = abs(float(t.get("fees") or 0))
        fee_per_unit = fees / qty if qty else 0.0

        remaining = qty
        # 1) close opposite-direction lots FIFO
        while remaining > 1e-9 and book and book[0]["dir"] != direction:
            lot = book[0]
            take = min(remaining, lot["qty"])
            frac = take / lot["qty"] if lot["qty"] else 0.0

            open_dollars = lot["unit_dollars"] * take
            open_fees = lot["fee_per_unit"] * take
            close_dollars = unit_dollars * take
            close_fees = fee_per_unit * take

            if lot["dir"] > 0:          # long: bought then sold
                cost, proceeds = open_dollars, close_dollars
                open_price, close_price = lot["price"], price
            else:                        # short: sold then bought back
                cost, proceeds = close_dollars, open_dollars
                open_price, close_price = lot["price"], price

            total_fees = round(open_fees + close_fees, 6)
            closed.append({
                "lot_id": lot_id_for(symbol, lot["ts"], _ts(t), take, [lot["txn_id"]], [str(t.get("id") or "")]),
                "symbol": symbol,
                "underlying": str(t.get("underlying") or lot["underlying"] or symbol.split(" ")[0]),
                "asset_type": asset_type or lot["asset_type"],
                "strategy_tag": str(lot.get("strategy_tag") or t.get("strategy_tag") or ""),
                "open_ts": lot["ts"],
                "close_ts": _ts(t),
                "qty": round(take, 6),
                "open_price": round(open_price, 6),
                "close_price": round(close_price, 6),
                "cost": round(cost, 2),
                "proceeds": round(proceeds, 2),
                "fees": round(total_fees, 2),
                "realized_pnl": round(proceeds - cost - total_fees, 2),
                "hold_days": hold_days_between(lot["ts"], _ts(t)),
                "open_txn_ids": [lot["txn_id"]],
                "close_txn_ids": [str(t.get("id") or "")],
                "direction": "long" if lot["dir"] > 0 else "short",
            })

            lot["qty"] = round(lot["qty"] - take, 9)
            remaining = round(remaining - take, 9)
            if lot["qty"] <= 1e-9:
                book.popleft()

        # 2) whatever is left opens a new lot (long or short)
        if remaining > 1e-9 and not expiry:
            book.append({
                "dir": direction,
                "qty": remaining,
                "unit_dollars": unit_dollars,
                "fee_per_unit": fee_per_unit,
                "price": price,
                "ts": _ts(t),
                "txn_id": str(t.get("id") or ""),
                "symbol": symbol,
                "underlying": str(t.get("underlying") or symbol.split(" ")[0]),
                "asset_type": asset_type,
                "strategy_tag": str(t.get("strategy_tag") or ""),
                "account_id": str(t.get("account_id") or ""),
                "source": str(t.get("source") or ""),
            })

    closed.sort(key=lambda l: (l["close_ts"], l["lot_id"]))
    open_book = {sym: [dict(l) for l in lots] for sym, lots in books.items() if lots}
    return closed, open_book


def match_lots(txns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FIFO-match every trade transaction into closed lots (models.ClosedLot dicts)."""
    closed, _ = replay(txns)
    return [{k: v for k, v in lot.items() if k != "direction"} for lot in closed]


def open_lots(txns: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Lots still open after replaying the tape, keyed by symbol."""
    _, book = replay(txns)
    return book
