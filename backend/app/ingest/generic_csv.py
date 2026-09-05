"""Generic CSV ingest for files that already use (roughly) our column names,
plus :func:`detect_source` — the sniffer that routes an upload to the right parser.

Column lookup is tolerant of case, spaces and underscores (via
``normalize.pick``), and anything missing is inferred: ``underlying`` from
``symbol``, ``asset_type`` from the symbol shape, ``amount`` from
``price * qty * multiplier`` signed by ``side``.
"""
from __future__ import annotations

import csv
import io
from typing import Any

from .normalize import (
    ASSET_TYPES,
    blank_row,
    clean_header,
    make_txn,
    parse_date,
    parse_money,
    parse_qty,
    parse_symbol_asset_type,
    pick,
    row_hash,
    strip_bom,
    underlying_of,
)

DEFAULT_SOURCE = "generic_csv"

_ASSET_ALIASES = {
    "equity": "stock", "equities": "stock", "stk": "stock", "stocks": "stock",
    "share": "stock", "shares": "stock", "etf": "stock", "fund": "stock",
    "opt": "option", "options": "option", "equityandindexoptions": "option",
    "cryptocurrency": "crypto", "cryptocurrencies": "crypto", "coin": "crypto",
    "div": "dividend", "dividends": "dividend",
    "int": "interest",
    "fees": "fee", "commission": "fee", "tax": "fee", "withholdingtax": "fee",
    "transfers": "transfer", "deposit": "transfer", "withdrawal": "transfer",
    "ach": "transfer", "wire": "transfer",
}

_BUY_WORDS = {"buy", "b", "bot", "bought", "purchase", "bto", "btc", "long"}
_SELL_WORDS = {"sell", "s", "sld", "sold", "sto", "stc", "short"}

# Cleaned header names that mark a Robinhood / IBKR export for detect_source().
_RH_MARKERS = {"transcode"}
_RH_PAIR = {"activitydate", "instrument"}
_IB_MARKERS = {"clientaccountid", "ibcommission", "opencloseindicator"}
_IB_LINE_PREFIXES = ("Trades,Header,", "Statement,Header,")


# ---------------------------------------------------------------- helpers

def _dedupe(header: list[str]) -> list[str]:
    """Make header names unique so ``dict(zip(...))`` cannot collapse columns."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in header:
        key = name or ""
        if key in seen:
            seen[key] += 1
            out.append(f"{key}.{seen[key]}")
        else:
            seen[key] = 0
            out.append(key)
    return out


def _first_rows(text: str) -> list[list[str]]:
    try:
        return list(csv.reader(io.StringIO(strip_bom(text or ""))))
    except Exception:
        return []


def _norm_asset_type(value: Any, symbol: str) -> str:
    c = clean_header(value)
    if c in ASSET_TYPES:
        return c
    if c in _ASSET_ALIASES:
        return _ASSET_ALIASES[c]
    if c:
        return "other"
    return parse_symbol_asset_type(symbol) if str(symbol or "").strip() else "other"


def _norm_side(value: Any) -> str:
    c = clean_header(value)
    if c in _BUY_WORDS:
        return "buy"
    if c in _SELL_WORDS:
        return "sell"
    return "none"


def _norm_open_close(value: Any) -> str:
    c = clean_header(value)
    if c.startswith("o"):
        return "open"
    if c.startswith("c"):
        return "close"
    return ""


def _blank(value: Any) -> bool:
    return str(value if value is not None else "").strip() == ""


# ---------------------------------------------------------------- main entry

def parse_generic_csv(text: str, source: str = DEFAULT_SOURCE, account_id: str = "") -> list[dict]:
    """Parse a CSV using our normalized column names into transaction dicts.

    Returns ``[]`` for empty/unparseable input; never raises. Rows without a
    parseable timestamp are skipped. Sorted by ``ts`` ascending.
    """
    try:
        rows = _first_rows(text)
        if not rows:
            return []
        header_idx = next((i for i, r in enumerate(rows) if any(str(c).strip() for c in r)), -1)
        if header_idx < 0:
            return []
        header = _dedupe([str(c).strip() for c in rows[header_idx]])
        out: list[dict] = []
        for cells in rows[header_idx + 1:]:
            try:
                txn = _row_to_txn(header, cells, source, account_id)
            except Exception:
                continue
            if txn is not None:
                out.append(txn)
    except Exception:
        return []
    out.sort(key=lambda t: t.get("ts") or "")
    return out


def _row_to_txn(header: list[str], cells: list[Any], source: str, account_id: str) -> dict | None:
    vals = [("" if c is None else str(c)) for c in cells]
    if len(vals) < len(header):
        vals += [""] * (len(header) - len(vals))
    elif len(vals) > len(header):
        vals = vals[:len(header)]
    row = dict(zip(header, vals))
    if blank_row(row):
        return None

    ts = parse_date(pick(row, "ts", "date", "datetime", "timestamp", "time", "trade_date"))
    if not ts:
        return None

    symbol = str(pick(row, "symbol", "ticker", "instrument") or "").strip()
    underlying = str(pick(row, "underlying", "underlying_symbol") or "").strip() or underlying_of(symbol)
    asset_type = _norm_asset_type(pick(row, "asset_type", "assettype", "asset class", "type"), symbol)
    side = _norm_side(pick(row, "side", "action", "buy_sell", "direction"))
    open_close = _norm_open_close(pick(row, "open_close", "openclose", "effect", "open/close"))

    qty = abs(parse_qty(pick(row, "qty", "quantity", "shares", "contracts", "units")))
    price = parse_money(pick(row, "price", "unit_price", "trade_price", "avg_price"))
    fees = abs(parse_money(pick(row, "fees", "fee", "commission", "comm")))

    amount_raw = pick(row, "amount", "net_amount", "cash", "value")
    if _blank(amount_raw):
        mult = 100.0 if asset_type == "option" else 1.0
        gross = qty * abs(price) * mult
        amount = -gross if side == "buy" else (gross if side == "sell" else 0.0)
    else:
        amount = parse_money(amount_raw)

    row_source = str(pick(row, "source") or "").strip() or (source or DEFAULT_SOURCE)
    row_account = str(pick(row, "account_id", "account", "accountid") or "").strip() or (account_id or "")
    external_id = str(pick(row, "external_id", "externalid", "order_id", "trade_id",
                           "transaction_id") or "").strip()

    digest = row_hash(*vals)
    row_id = str(pick(row, "id", "txn_id") or "").strip() or ("gen:" + digest[:16])

    return make_txn(
        id=row_id,
        source=row_source,
        account_id=row_account,
        ts=ts,
        symbol=symbol,
        underlying=underlying,
        asset_type=asset_type,
        side=side,
        open_close=open_close,
        qty=qty,
        price=price,
        fees=fees,
        amount=amount,
        description=str(pick(row, "description", "desc", "memo", "note") or "").strip(),
        external_id=external_id or digest,
        strategy_tag=str(pick(row, "strategy_tag", "strategy", "tag") or "").strip(),
        raw=row,
    )


# ---------------------------------------------------------------- sniffer

def detect_source(text: str) -> str:
    """Sniff which parser a CSV belongs to.

    Returns 'robinhood' | 'ibkr_flex' | 'account_summary' | 'generic'.

    BOM tolerant and tolerant of leading blank lines. Never raises.
    """
    try:
        cleaned = strip_bom(text or "")
        if not cleaned.strip():
            return "generic"

        lines = cleaned.splitlines()
        header_line = next((ln for ln in lines if ln.strip()), "")
        try:
            cols = {clean_header(c) for c in next(csv.reader(io.StringIO(header_line)))}
        except Exception:
            cols = {clean_header(c) for c in header_line.split(",")}

        if cols & _RH_MARKERS or _RH_PAIR <= cols:
            return "robinhood"
        if cols & _IB_MARKERS:
            return "ibkr_flex"
        for ln in lines[:400]:
            stripped = ln.lstrip()
            if stripped.startswith(_IB_LINE_PREFIXES):
                return "ibkr_flex"
        # a monthly account-summary statement: equity-like column + period column
        from app.ingest.account_summary_csv import looks_like_account_summary
        if looks_like_account_summary(cleaned):
            return "account_summary"
        return "generic"
    except Exception:
        return "generic"
