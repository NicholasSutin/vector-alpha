"""Deterministic synthetic demo book (account DEMO-1, source "synthetic").

Eight months, 2026-01 → 2026-08, of a realistic retail options+equities book.
The book is engineered so a *specific story* is provable from the numbers:

  Jan–May   steady, modestly positive, ~20 trades/month, ~8-10 day holds
  June      the best month (~+$1,800), carried by AMD and NVDA swing calls
  July      the bad month (~-$800, a ~-$2,600 swing) — ~70% of the decline is
            three NVDA calls opened the week before earnings (strategy
            "earnings"), on top of over-trading: trades ~+85%, hold time
            collapses to ~2 days, options share ~70%, NVDA concentration ~58%,
            fees ~+50%
  August    partial recovery: fewer trades, back to swing holds

Also included: one option expiring worthless (OEXP), one small BTC round trip,
monthly deposits, dividends, interest and platform fees.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from app.ingest.normalize import make_txn, option_symbol

ACCOUNT_ID = "DEMO-1"
SOURCE = "synthetic"
PERIODS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]

# monthly realized-P&L targets that encode the story
MONTH_TARGET = {
    "2026-01": 380.0, "2026-02": 265.0, "2026-03": 510.0, "2026-04": 205.0,
    "2026-05": 470.0, "2026-06": 1800.0, "2026-07": -800.0, "2026-08": 640.0,
}

STOCK_BASE = {"AAPL": 228.0, "MSFT": 415.0, "SPY": 560.0, "AMD": 168.0, "PLTR": 42.0, "SOFI": 9.6}
STOCK_DRIFT = {"AAPL": 3.1, "MSFT": 5.4, "SPY": 6.2, "AMD": 4.8, "PLTR": 1.4, "SOFI": 0.22}

OPTION_FEE_PER_CONTRACT = 0.65
STOCK_SELL_FEE = 0.03


# ---------------------------------------------------------------- calendar

def _business_days(period: str) -> list[date]:
    year, month = int(period[:4]), int(period[5:7])
    d = date(year, month, 1)
    out = []
    while d.month == month:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _ts(d: date, hour: int, minute: int) -> str:
    return datetime(d.year, d.month, d.day, hour, minute, 0).isoformat(timespec="seconds")


def _third_friday(period: str) -> date:
    fridays = [d for d in _business_days(period) if d.weekday() == 4]
    return fridays[2] if len(fridays) >= 3 else fridays[-1]


def _expiry_for(period: str, months_out: int = 1) -> date:
    year, month = int(period[:4]), int(period[5:7]) + months_out
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return _third_friday(f"{year:04d}-{month:02d}")


# ---------------------------------------------------------------- allocation

def _alloc(rnd: random.Random, n: int, total: float, wins: int) -> list[float]:
    """n per-trip P&L targets with exactly `wins` positive values, summing to `total`."""
    if n <= 0:
        return []
    wins = max(0, min(wins, n))
    raw = [round(rnd.uniform(55, 340), 2) for _ in range(wins)]
    raw += [-round(rnd.uniform(45, 260), 2) for _ in range(n - wins)]
    rnd.shuffle(raw)
    pos = sum(v for v in raw if v > 0)
    neg = sum(v for v in raw if v < 0)
    target_pos = total - neg
    if pos > 0 and target_pos > 0:
        k = target_pos / pos
        raw = [round(v * k, 2) if v > 0 else v for v in raw]
    elif neg < 0:
        k = (total - pos) / neg if neg else 1.0
        raw = [round(v * k, 2) if v < 0 else v for v in raw]
    drift = total - sum(raw)
    if raw:
        idx = max(range(len(raw)), key=lambda i: abs(raw[i]))
        raw[idx] = round(raw[idx] + drift, 2)
    return raw


# ---------------------------------------------------------------- builders

class Book:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, **kw: Any) -> None:
        raw = {k: v for k, v in kw.items() if k not in ("raw",)}
        payload = "|".join(f"{k}={raw.get(k)}" for k in sorted(raw))
        kw.setdefault("id", "syn_" + hashlib.sha1(payload.encode()).hexdigest()[:16])
        kw.setdefault("source", SOURCE)
        kw.setdefault("account_id", ACCOUNT_ID)
        kw.setdefault("external_id", kw["id"])
        self.rows.append(make_txn(raw={k: v for k, v in raw.items() if k != "id"}, **kw))

    def trade(self, *, ts: str, symbol: str, underlying: str, asset_type: str, side: str,
              open_close: str, qty: float, price: float, strategy: str, description: str) -> None:
        mult = 100.0 if asset_type == "option" else 1.0
        gross = round(price * qty * mult, 2)
        if asset_type == "option":
            fees = round(OPTION_FEE_PER_CONTRACT * qty + (0.04 if side == "sell" else 0.0), 2)
        elif asset_type == "crypto":
            fees = 0.0
        else:
            fees = STOCK_SELL_FEE if side == "sell" else 0.0
        self.add(ts=ts, symbol=symbol, underlying=underlying, asset_type=asset_type, side=side,
                 open_close=open_close, qty=qty, price=price, fees=fees,
                 amount=(gross if side == "sell" else -gross),
                 description=description, strategy_tag=strategy)

    def round_trip(self, *, symbol: str, underlying: str, asset_type: str, strategy: str,
                   open_d: date, close_d: date, qty: float, open_price: float,
                   target_pnl: float, split: bool = False) -> None:
        """Open then close so realized P&L lands on `target_pnl` (±rounding)."""
        mult = 100.0 if asset_type == "option" else 1.0
        if asset_type == "option":
            fees = OPTION_FEE_PER_CONTRACT * qty * 2 + 0.04
        elif asset_type == "crypto":
            fees = 0.0
        else:
            fees = STOCK_SELL_FEE
        close_price = open_price + (target_pnl + fees) / (qty * mult)
        close_price = max(round(close_price, 2), 0.01)
        label = "call" if " C " in symbol else ("put" if " P " in symbol else asset_type)

        self.trade(ts=_ts(open_d, 10, 31 + (open_d.day % 20)), symbol=symbol, underlying=underlying,
                   asset_type=asset_type, side="buy", open_close="open", qty=qty, price=open_price,
                   strategy=strategy, description=f"Buy to open {symbol} {label}")
        if split and qty >= 2:
            first = round(qty / 2, 4) if asset_type != "option" else max(1, int(qty // 2))
            rest = round(qty - first, 4)
            mid = close_d - timedelta(days=1)
            while mid.weekday() >= 5 or mid <= open_d:
                mid += timedelta(days=1)
            self.trade(ts=_ts(mid, 11, 5), symbol=symbol, underlying=underlying,
                       asset_type=asset_type, side="sell", open_close="close", qty=first,
                       price=close_price, strategy=strategy,
                       description=f"Sell to close {symbol} (partial)")
            self.trade(ts=_ts(close_d, 14, 12), symbol=symbol, underlying=underlying,
                       asset_type=asset_type, side="sell", open_close="close", qty=rest,
                       price=close_price, strategy=strategy,
                       description=f"Sell to close {symbol}")
        else:
            self.trade(ts=_ts(close_d, 14, 12 + (close_d.day % 30)), symbol=symbol,
                       underlying=underlying, asset_type=asset_type, side="sell",
                       open_close="close", qty=qty, price=close_price, strategy=strategy,
                       description=f"Sell to close {symbol}")


# ---------------------------------------------------------------- schedule

def _place(days: list[date], i: int, n: int, hold: int) -> tuple[date, date]:
    """Spread `n` entries across the first ~2/3 of the month; clamp the exit inside it."""
    span = max(1, int(len(days) * 0.62))
    open_idx = min(span - 1, int(round(i * (span - 1) / max(1, n - 1)))) if n > 1 else 0
    close_idx = min(len(days) - 1, open_idx + max(1, hold))
    return days[open_idx], days[close_idx]


def _stock_price(sym: str, month_idx: int, rnd: random.Random) -> float:
    return round(STOCK_BASE[sym] + STOCK_DRIFT[sym] * month_idx + rnd.uniform(-2.5, 2.5), 2)


def _call(underlying: str, period: str, strike_mult: float, base: float, months_out: int = 1) -> str:
    strike = round(base * strike_mult / 5) * 5
    return option_symbol(underlying, _expiry_for(period, months_out), "C", strike)


# ---------------------------------------------------------------- generator

def generate_synthetic_book(seed: int = 7) -> list[dict[str, Any]]:
    """Build the full demo book. Deterministic for a given seed."""
    rnd = random.Random(seed)
    book = Book()

    stock_names = list(STOCK_BASE)

    for month_idx, period in enumerate(PERIODS):
        days = _business_days(period)
        target = MONTH_TARGET[period]

        # ---- cash: deposits, interest, platform fee, dividends -------------
        book.add(ts=_ts(days[1], 9, 5), asset_type="transfer", side="none", amount=2000.0,
                 description="ACH deposit from Chase ****4021")
        if month_idx in (1, 3, 5, 6):
            book.add(ts=_ts(days[len(days) // 2], 9, 5), asset_type="transfer", side="none",
                     amount=750.0, description="ACH deposit (bonus)")
        if period == "2026-08":
            book.add(ts=_ts(days[-4], 9, 30), asset_type="transfer", side="none", amount=-1200.0,
                     description="ACH withdrawal to Chase ****4021")
        book.add(ts=_ts(days[-1], 20, 0), asset_type="interest", side="none",
                 amount=round(3.4 + month_idx * 0.65, 2), description="Cash sweep interest")
        book.add(ts=_ts(days[0], 8, 0), asset_type="fee", side="none", amount=-5.0, fees=5.0,
                 description="Gold subscription fee")
        if month_idx in (0, 2, 4, 6):
            book.add(ts=_ts(days[-3], 8, 0), asset_type="fee", side="none", amount=-2.5, fees=2.5,
                     description="Regulatory / ADR fee")
        if period in ("2026-03", "2026-06"):
            book.add(ts=_ts(days[12], 9, 0), symbol="SPY", underlying="SPY", asset_type="dividend",
                     side="none", amount=round(41.2 + month_idx * 1.8, 2),
                     description="SPY cash dividend")
            book.add(ts=_ts(days[14], 9, 0), symbol="MSFT", underlying="MSFT",
                     asset_type="dividend", side="none", amount=round(18.6 + month_idx, 2),
                     description="MSFT cash dividend")
        if period in ("2026-02", "2026-05", "2026-08"):
            book.add(ts=_ts(days[9], 9, 0), symbol="AAPL", underlying="AAPL", asset_type="dividend",
                     side="none", amount=round(22.4 + month_idx * 1.1, 2),
                     description="AAPL cash dividend")
        if period in ("2026-02", "2026-03", "2026-05", "2026-06", "2026-08"):
            book.add(ts=_ts(days[-2], 8, 30), asset_type="fee", side="none", amount=-1.35,
                     fees=1.35, description="Dividend withholding tax")

        # ---- trades --------------------------------------------------------
        if period == "2026-06":
            _june(book, rnd, period, days, month_idx)
        elif period == "2026-07":
            _july(book, rnd, period, days, month_idx)
        elif period == "2026-08":
            _august(book, rnd, period, days, month_idx, target)
        else:
            _steady_month(book, rnd, period, days, month_idx, target, stock_names)

    book.rows.sort(key=lambda t: (t["ts"], t["id"]))
    return book.rows


def _steady_month(book: Book, rnd: random.Random, period: str, days: list[date],
                  month_idx: int, target: float, stock_names: list[str]) -> None:
    """Jan–May: ~10 round trips, mostly stock swings, 8-10 day holds."""
    extras = 0.0

    # one option expiring worthless in May (OEXP path)
    if period == "2026-05":
        exp = _third_friday("2026-05")
        sym = option_symbol("SOFI", exp, "C", 12)
        open_d = days[2]
        book.trade(ts=_ts(open_d, 10, 40), symbol=sym, underlying="SOFI", asset_type="option",
                   side="buy", open_close="open", qty=4, price=0.45, strategy="momentum",
                   description=f"Buy to open {sym} call")
        book.add(ts=_ts(exp, 16, 0), symbol=sym, underlying="SOFI", asset_type="option",
                 side="none", open_close="close", qty=4, price=0.0, amount=0.0,
                 strategy_tag="momentum", description=f"Option Expiration for {sym} (worthless)")
        extras += -(0.45 * 4 * 100) - 2.6

    # one small BTC round trip in February
    if period == "2026-02":
        book.round_trip(symbol="BTC", underlying="BTC", asset_type="crypto", strategy="swing",
                        open_d=days[3], close_d=days[11], qty=0.05, open_price=68420.0,
                        target_pnl=118.0)
        extras += 118.0

    n_stock, n_opt = 7, 3
    pool = target - extras
    stock_pool = round(pool * 0.62, 2)
    opt_pool = round(pool - stock_pool, 2)

    stock_targets = _alloc(rnd, n_stock, stock_pool, wins=5)
    opt_targets = _alloc(rnd, n_opt, opt_pool, wins=2)

    for i in range(n_stock):
        sym = stock_names[(i + month_idx) % len(stock_names)]
        price = _stock_price(sym, month_idx, rnd)
        qty = max(2, round(2100 / price))
        open_d, close_d = _place(days, i, n_stock, hold=rnd.choice([5, 6, 7, 8]))
        book.round_trip(symbol=sym, underlying=sym, asset_type="stock", strategy="swing",
                        open_d=open_d, close_d=close_d, qty=qty, open_price=price,
                        target_pnl=stock_targets[i], split=(i in (2, 5)))

    for i in range(n_opt):
        und = ["NVDA", "TSLA", "AMD"][i]
        base = {"NVDA": 148.0, "TSLA": 262.0, "AMD": 168.0}[und] + month_idx * 4
        sym = _call(und, period, 1.04, base)
        open_d, close_d = _place(days, i, n_opt, hold=rnd.choice([4, 5, 6, 7]))
        book.round_trip(symbol=sym, underlying=und, asset_type="option", strategy="swing",
                        open_d=open_d, close_d=close_d, qty=rnd.choice([3, 4]),
                        open_price=round(rnd.uniform(3.2, 5.4), 2), target_pnl=opt_targets[i])


def _june(book: Book, rnd: random.Random, period: str, days: list[date], month_idx: int) -> None:
    """The best month: AMD + NVDA swing calls carry it. Total ≈ +1,800."""
    # NVDA swing calls +590 total
    nvda_specs = [(4, 4.10, 330.0, 5), (5, 3.60, 260.0, 6)]
    for i, (qty, px, tgt, hold) in enumerate(nvda_specs):
        open_d, close_d = _place(days, i, 2, hold)
        book.round_trip(symbol=_call("NVDA", period, 1.05, 168.0), underlying="NVDA",
                        asset_type="option", strategy="swing", open_d=open_d, close_d=close_d,
                        qty=qty, open_price=px, target_pnl=tgt)
    # AMD swing calls +720 total
    amd_specs = [(6, 3.35, 430.0, 7), (5, 3.90, 290.0, 5)]
    for i, (qty, px, tgt, hold) in enumerate(amd_specs):
        open_d, close_d = _place(days, i + 1, 4, hold)
        book.round_trip(symbol=_call("AMD", period, 1.05, 192.0), underlying="AMD",
                        asset_type="option", strategy="swing", open_d=open_d, close_d=close_d,
                        qty=qty, open_price=px, target_pnl=tgt)
    # one TSLA call +180
    open_d, close_d = _place(days, 2, 4, 6)
    book.round_trip(symbol=_call("TSLA", period, 1.06, 286.0), underlying="TSLA",
                    asset_type="option", strategy="momentum", open_d=open_d, close_d=close_d,
                    qty=4, open_price=4.80, target_pnl=180.0)
    # stock core +310 across 7 swings
    names = ["AAPL", "MSFT", "SPY", "AMD", "PLTR", "SOFI", "AAPL"]
    targets = _alloc(rnd, 7, 310.0, wins=5)
    for i, sym in enumerate(names):
        price = _stock_price(sym, month_idx, rnd)
        qty = max(2, round(2100 / price))
        open_d, close_d = _place(days, i, 7, hold=rnd.choice([5, 6, 7, 8]))
        book.round_trip(symbol=sym, underlying=sym, asset_type="stock", strategy="swing",
                        open_d=open_d, close_d=close_d, qty=qty, open_price=price,
                        target_pnl=targets[i], split=(i in (1, 4)))


def _july(book: Book, rnd: random.Random, period: str, days: list[date], month_idx: int) -> None:
    """The bad month: three NVDA earnings calls (-1,230) plus frantic churn."""
    earnings_day = days[min(len(days) - 4, 16)]

    # --- three NVDA calls opened the week before earnings: -1,230 ----------
    nvda_earn = [(10, 4.60, -520.0), (8, 5.10, -430.0), (7, 3.95, -280.0)]
    for i, (qty, px, tgt) in enumerate(nvda_earn):
        open_d = days[max(0, 12 + i)]
        close_d = days[min(len(days) - 1, 15 + i)]
        book.round_trip(symbol=_call("NVDA", period, 1.08, 176.0 + i * 4), underlying="NVDA",
                        asset_type="option", strategy="earnings", open_d=open_d, close_d=close_d,
                        qty=qty, open_price=px, target_pnl=tgt)

    # --- six NVDA momentum scalps that net ~zero --------------------------
    momentum = [140.0, -120.0, 90.0, -110.0, 60.0, -60.0]
    for i, tgt in enumerate(momentum):
        open_d = days[min(len(days) - 2, i * 2)]
        close_d = days[min(len(days) - 1, i * 2 + 1)]
        book.round_trip(symbol=_call("NVDA", period, 1.03, 172.0 + i * 2), underlying="NVDA",
                        asset_type="option", strategy="momentum", open_d=open_d, close_d=close_d,
                        qty=5, open_price=round(2.60 + i * 0.18, 2), target_pnl=tgt)

    # --- TSLA options chop -150, AMD options +180 -------------------------
    tsla_targets = _alloc(rnd, 4, -150.0, wins=1)
    for i, tgt in enumerate(tsla_targets):
        open_d = days[min(len(days) - 2, 1 + i * 4)]
        close_d = days[min(len(days) - 1, 2 + i * 4)]
        book.round_trip(symbol=_call("TSLA", period, 1.05, 292.0 + i * 3), underlying="TSLA",
                        asset_type="option", strategy="momentum", open_d=open_d, close_d=close_d,
                        qty=4, open_price=round(4.20 + i * 0.3, 2), target_pnl=tgt)

    amd_targets = _alloc(rnd, 3, 180.0, wins=2)
    for i, tgt in enumerate(amd_targets):
        open_d = days[min(len(days) - 2, 2 + i * 5)]
        close_d = days[min(len(days) - 1, 3 + i * 5)]
        book.round_trip(symbol=_call("AMD", period, 1.04, 196.0 + i * 3), underlying="AMD",
                        asset_type="option", strategy="momentum", open_d=open_d, close_d=close_d,
                        qty=4, open_price=round(2.90 + i * 0.25, 2), target_pnl=tgt)

    # --- a NVDA long opened late July and NOT closed: the lingering ------
    # concentration the agent's "cut NVDA exposure" advisement can act on
    book.trade(ts=_ts(days[min(len(days) - 1, 18)], 15, 42), symbol="NVDA", underlying="NVDA",
               asset_type="stock", side="buy", open_close="open", qty=6, price=181.40,
               strategy="momentum", description="Buy NVDA common stock (averaging down)")

    # --- six quick stock flips +400 ---------------------------------------
    names = ["AAPL", "MSFT", "SPY", "PLTR", "SOFI", "AMD"]
    targets = _alloc(rnd, 6, 400.0, wins=4)
    for i, sym in enumerate(names):
        price = _stock_price(sym, month_idx, rnd)
        qty = max(2, round(900 / price))
        open_d = days[min(len(days) - 2, 1 + i * 3)]
        close_d = days[min(len(days) - 1, 2 + i * 3)]
        book.round_trip(symbol=sym, underlying=sym, asset_type="stock", strategy="momentum",
                        open_d=open_d, close_d=close_d, qty=qty, open_price=price,
                        target_pnl=targets[i], split=(i == 4))


def _august(book: Book, rnd: random.Random, period: str, days: list[date],
            month_idx: int, target: float) -> None:
    """Partial recovery: fewer trades, swing holds return."""
    opt_targets = _alloc(rnd, 3, 240.0, wins=2)
    for i, tgt in enumerate(opt_targets):
        und = ["NVDA", "AMD", "SPY"][i]
        base = {"NVDA": 186.0, "AMD": 204.0, "SPY": 604.0}[und]
        open_d, close_d = _place(days, i, 3, hold=rnd.choice([6, 7, 8]))
        book.round_trip(symbol=_call(und, period, 1.04, base), underlying=und, asset_type="option",
                        strategy="swing", open_d=open_d, close_d=close_d, qty=3,
                        open_price=round(rnd.uniform(3.4, 5.0), 2), target_pnl=tgt)

    names = ["AAPL", "MSFT", "SPY", "AMD", "PLTR", "SOFI", "AAPL", "MSFT"]
    targets = _alloc(rnd, 8, target - 240.0, wins=6)
    for i, sym in enumerate(names):
        price = _stock_price(sym, month_idx, rnd)
        qty = max(2, round(2300 / price))
        open_d, close_d = _place(days, i, 8, hold=rnd.choice([6, 7, 8, 9]))
        book.round_trip(symbol=sym, underlying=sym, asset_type="stock", strategy="swing",
                        open_d=open_d, close_d=close_d, qty=qty, open_price=price,
                        target_pnl=targets[i], split=(i == 3))

    # --- core longs left open at the end of the book -----------------------
    for sym, shares, day_idx in (("AAPL", 10, 4), ("MSFT", 5, 7), ("SPY", 8, 10)):
        book.trade(ts=_ts(days[day_idx], 10, 12), symbol=sym, underlying=sym, asset_type="stock",
                   side="buy", open_close="open", qty=shares,
                   price=_stock_price(sym, month_idx, rnd), strategy="income",
                   description=f"Buy {sym} — core long (held)")
    # one open SOFI call
    sofi_call = option_symbol("SOFI", _expiry_for(period, 2), "C", 12)
    book.trade(ts=_ts(days[6], 11, 20), symbol=sofi_call, underlying="SOFI", asset_type="option",
               side="buy", open_close="open", qty=2, price=0.62, strategy="swing",
               description=f"Buy to open {sofi_call} call (held)")


# marks used to value still-open positions at each month end
MARKS = {"AAPL": 1.035, "MSFT": 1.021, "SPY": 1.014, "NVDA": 0.882, "SOFI": 1.24,
         "AMD": 1.028, "PLTR": 0.97, "TSLA": 1.01, "BTC": 1.03}


def generate_synthetic_snapshots(txns: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Month-end equity/cash/positions snapshots, derived from the demo book itself.

    Open positions are marked with a fixed per-underlying factor so
    market_value / unrealized_pnl are internally consistent with the tape.
    """
    from app.analytics.ledger import match_lots, multiplier_for, open_lots

    txns = list(txns) if txns is not None else generate_synthetic_book()
    equity_base, cash_base = 24000.0, 6200.0
    out: list[dict[str, Any]] = []

    for period in PERIODS:
        cutoff = _ts(_business_days(period)[-1], 20, 30)
        subset = [t for t in txns if t["ts"] <= cutoff]
        realized = sum(float(l["realized_pnl"]) for l in match_lots(subset))
        cash_moves = sum(float(t["amount"]) for t in subset
                         if t["asset_type"] in ("transfer", "dividend", "interest", "fee"))
        deposits = sum(float(t["amount"]) for t in subset if t["asset_type"] == "transfer")

        positions, unrealized, mkt_total = [], 0.0, 0.0
        for symbol, lots in open_lots(subset).items():
            qty = sum(l["dir"] * l["qty"] for l in lots)
            if abs(qty) < 1e-9:
                continue
            asset_type = lots[0]["asset_type"]
            mult = multiplier_for(asset_type)
            units = sum(l["qty"] for l in lots) or 1.0
            avg_cost = sum(l["unit_dollars"] * l["qty"] for l in lots) / units / (mult or 1.0)
            und = str(lots[0].get("underlying") or symbol.split(" ")[0]).upper()
            last = round(avg_cost * MARKS.get(und, 1.0), 4)
            mv = round(last * qty * mult, 2)
            cost = round(avg_cost * qty * mult, 2)
            unrealized += mv - cost
            mkt_total += mv
            positions.append({
                "symbol": symbol, "underlying": und, "asset_type": asset_type,
                "qty": round(qty, 6), "avg_cost": round(avg_cost, 4), "last": last,
                "market_value": mv, "unrealized_pnl": round(mv - cost, 2),
            })
        positions.sort(key=lambda p: (p["asset_type"], p["symbol"]))

        cash = round(cash_base + cash_moves + realized - mkt_total + deposits * 0.0, 2)
        out.append({
            "id": "snap_" + hashlib.sha1(f"{SOURCE}|{period}".encode()).hexdigest()[:12],
            "source": SOURCE,
            "account_id": ACCOUNT_ID,
            "as_of": cutoff,
            "equity": round(equity_base + cash_moves + realized + unrealized, 2),
            "cash": cash,
            "positions_json": json.dumps(positions),
        })
    return out
