"""IBKR CSV -> normalized transaction dicts.

Handles two very different exports:

* **Flat Flex Query "Trades" CSV** — one header row with columns such as
  ``ClientAccountID, TradeDate, Symbol, AssetClass, Buy/Sell, Quantity,
  TradePrice, Proceeds, IBCommission, NetCash, Open/CloseIndicator, Strike,
  Expiry, Put/Call, UnderlyingSymbol, Multiplier, TransactionID``.
* **Activity Statement CSV** — every line is prefixed with its section name and
  a ``Header``/``Data`` discriminator, e.g. ``Trades,Header,...`` /
  ``Trades,Data,Order,Stocks,USD,AAPL,...``.

In both shapes the non-trade cash sections (Deposits & Withdrawals, Dividends,
Withholding Tax, Interest, Fees, Cash Transactions) are parsed best effort.

``amount`` is kept **gross**-signed (buys negative, sells positive); commissions
live in ``fees``. Nothing here raises on malformed input — bad rows are skipped.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any

from .normalize import (
    blank_row,
    clean_header,
    make_txn,
    option_symbol,
    parse_date,
    parse_money,
    parse_qty,
    pick,
    row_hash,
    strip_bom,
    to_iso_date,
    underlying_of,
)

SOURCE = "ibkr_flex"

# Cleaned header names that identify a flat Flex "Trades" export.
_FLAT_HEADERS = {
    "clientaccountid", "accountid", "tradedate", "datetime", "symbol", "assetclass",
    "buysell", "quantity", "tradeprice", "proceeds", "ibcommission", "netcash",
    "opencloseindicator", "strike", "expiry", "putcall", "underlyingsymbol",
    "multiplier", "fifopnlrealized", "transactionid", "tradeid", "description",
    "currency", "conid", "orderid", "expirydate", "reportdate",
}

_ASSET_CLASS = {
    "STK": "stock",
    "OPT": "option",
    "FOP": "option",
    "IOPT": "option",
    "CASH": "other",
    "CRYPTO": "crypto",
    "FUT": "other",
    "BOND": "other",
    "FUND": "stock",
    "WAR": "other",
}

_DDMMMYY = re.compile(r"^(\d{1,2})([A-Za-z]{3})(\d{2,4})$")
_OCC = re.compile(r"^(\d{6})([CP])(\d{8})$")
_STRIKE_TOKEN = re.compile(r"^\$?[0-9,]+(?:\.\d+)?$")
_DIV_SYMBOL = re.compile(r"^([A-Z0-9\.\-]{1,12})\s*\(")


# ---------------------------------------------------------------- small utils

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


def _fit(cells: list[Any], header: list[str]) -> list[str]:
    """Pad/truncate a data row so it lines up with its header."""
    vals = [("" if c is None else str(c)) for c in cells]
    if len(vals) < len(header):
        vals += [""] * (len(header) - len(vals))
    elif len(vals) > len(header):
        vals = vals[:len(header)]
    return vals


def _ts(value: Any) -> str | None:
    """IBKR timestamps: '2026-07-01, 10:31:02', '20260701;103102', '20260701'."""
    s = str(value or "").strip().strip('"')
    if not s:
        return None
    flattened = re.sub(r"^(\S+),\s*", r"\1 ", s)
    return parse_date(flattened) or parse_date(s)


def _expiry_iso(value: Any) -> str:
    """'20260620' / '06/20/26' / '20JUN26' -> 'YYYY-MM-DD' ('' if unparseable)."""
    s = str(value or "").strip()
    if not s:
        return ""
    iso = to_iso_date(s)
    if iso:
        return iso
    m = _DDMMMYY.match(s)
    if m:
        day, mon, yr = m.groups()
        yr = f"20{yr}" if len(yr) == 2 else yr
        try:
            return datetime.strptime(f"{day} {mon.title()} {yr}", "%d %b %Y").date().isoformat()
        except ValueError:
            return ""
    return ""


def _parse_option_text(raw: str) -> tuple[str, str]:
    """Best-effort IBKR option symbol -> (canonical symbol, underlying).

    Understands ``NVDA 20JUN26 140 C``, ``NVDA 06/20/26 140 C``,
    ``NVDA 20JUN26 C 140`` and the OCC-ish ``NVDA  260620C00140000``.
    Falls back to (raw string, first token).
    """
    s = " ".join(str(raw or "").split())
    if not s:
        return "", ""
    toks = s.split(" ")
    und = toks[0].upper()

    if len(toks) >= 4:
        exp = _expiry_iso(toks[1])
        cp = ""
        strike = ""
        for t in (toks[2], toks[3]):
            tu = t.strip().upper()
            if tu in ("C", "P", "CALL", "PUT"):
                cp = tu
            elif _STRIKE_TOKEN.match(t.strip()):
                strike = t.strip()
        if exp and cp and strike:
            return option_symbol(und, exp, cp, strike), und

    if len(toks) == 2:
        m = _OCC.match(toks[1].upper())
        if m:
            ymd, cp, strike_raw = m.groups()
            exp = _expiry_iso("20" + ymd)
            if exp:
                return option_symbol(und, exp, cp, int(strike_raw) / 1000.0), und

    return s, und


def _open_close(code: Any) -> str:
    """IBKR Code / Open-CloseIndicator -> 'open' | 'close' | ''."""
    tokens = {t.strip().upper() for t in re.split(r"[;,\s]+", str(code or "")) if t.strip()}
    if "O" in tokens:
        return "open"
    if "C" in tokens:
        return "close"
    if "EX" in tokens or "A" in tokens or "ASSIGN" in tokens or "EXPI" in tokens:
        return "close"
    return ""


def _side(explicit: Any, qty: float) -> str:
    s = clean_header(explicit)
    if s.startswith("buy") or s == "b" or s == "bot":
        return "buy"
    if s.startswith("sell") or s == "s" or s == "sld":
        return "sell"
    if qty < 0:
        return "sell"
    if qty > 0:
        return "buy"
    return "none"


def _gross_amount(side: str, qty: float, price: float, mult: float,
                  net_cash: str, proceeds: str) -> float:
    """Gross-signed cash flow; falls back to NetCash / Proceeds when no price."""
    gross = abs(qty) * abs(price) * abs(mult or 1)
    if gross:
        return -gross if side == "buy" else (gross if side == "sell" else 0.0)
    if str(net_cash).strip():
        return parse_money(net_cash)
    if str(proceeds).strip():
        return parse_money(proceeds)
    return 0.0


# ---------------------------------------------------------------- main entry

def parse_ibkr_flex_csv(text: str, account_id: str | None = None) -> list[dict]:
    """Parse an IBKR Flex-Query or Activity-Statement CSV into transaction dicts.

    Returns ``[]`` for empty/unrecognizable input; never raises.
    """
    try:
        cleaned = strip_bom(text or "")
        if not cleaned.strip():
            return []
        sections = _read_sections(cleaned)
        if sections:
            out = _parse_statement(sections, account_id)
        else:
            out = _parse_flat(cleaned, account_id)
    except Exception:
        return []
    out.sort(key=lambda t: t.get("ts") or "")
    return out


# ---------------------------------------------------------------- shape A: flat

def _read_flat_table(text: str) -> tuple[list[str], list[list[str]]]:
    try:
        rows = [r for r in csv.reader(io.StringIO(text))]
    except Exception:
        return [], []
    for i, row in enumerate(rows[:50]):
        cols = {clean_header(c) for c in row}
        if len(cols & _FLAT_HEADERS) >= 3:
            return _dedupe([str(c).strip() for c in row]), rows[i + 1:]
    return [], []


def _parse_flat(text: str, account_id: str | None) -> list[dict]:
    """Parse a flat Flex-Query trades CSV."""
    header, data_rows = _read_flat_table(text)
    if not header:
        return []
    out: list[dict] = []
    for cells in data_rows:
        try:
            txn = _flat_row(header, cells, account_id)
        except Exception:
            continue
        if txn is not None:
            out.append(txn)
    return out


def _flat_row(header: list[str], cells: list[Any], account_id: str | None) -> dict | None:
    vals = _fit(cells, header)
    row = dict(zip(header, vals))
    if blank_row(row):
        return None

    ts = _ts(pick(row, "DateTime", "Date/Time", "TradeDate", "ReportDate", "SettleDate"))
    if not ts:
        return None

    asset_class = str(pick(row, "AssetClass", "AssetCategory") or "").strip().upper()
    asset_type = _ASSET_CLASS.get(asset_class, "other")

    qty_signed = parse_qty(pick(row, "Quantity"))
    qty = abs(qty_signed)
    side = _side(pick(row, "Buy/Sell", "BuySell", "Side"), qty_signed)
    price = abs(parse_money(pick(row, "TradePrice", "T. Price", "Price")))

    mult_raw = pick(row, "Multiplier")
    mult = parse_money(mult_raw) or (100.0 if asset_type == "option" else 1.0)

    symbol = str(pick(row, "Symbol") or "").strip()
    underlying = str(pick(row, "UnderlyingSymbol") or "").strip().upper()
    if asset_type == "option":
        root = underlying or underlying_of(symbol)
        expiry = _expiry_iso(pick(row, "Expiry", "ExpiryDate", "Expiration"))
        strike = pick(row, "Strike")
        cp = str(pick(row, "Put/Call", "PutCall", "Right") or "").strip()
        if root and expiry and cp and str(strike).strip():
            symbol = option_symbol(root, expiry, cp, strike)
            underlying = root
        else:
            symbol, parsed_und = _parse_option_text(symbol)
            underlying = underlying or parsed_und
    else:
        symbol = symbol.upper()
        underlying = underlying or underlying_of(symbol)

    fees = abs(parse_money(pick(row, "IBCommission", "Commission", "Comm/Fee")))
    amount = _gross_amount(
        side, qty, price, mult,
        str(pick(row, "NetCash") or ""), str(pick(row, "Proceeds") or ""),
    )

    acct = str(pick(row, "ClientAccountID", "AccountId", "Account") or "").strip()
    external_id = str(pick(row, "TransactionID", "TradeID", "OrderID") or "").strip() or row_hash(*vals)

    return make_txn(
        id="ib:" + row_hash(*vals)[:16],
        source=SOURCE,
        account_id=acct or (account_id or ""),
        ts=ts,
        symbol=symbol,
        underlying=underlying,
        asset_type=asset_type,
        side=side,
        open_close=_open_close(pick(row, "Open/CloseIndicator", "OpenCloseIndicator", "Code")),
        qty=qty,
        price=price,
        fees=fees,
        amount=amount,
        description=str(pick(row, "Description") or "").strip(),
        external_id=external_id,
        raw=row,
    )


# ---------------------------------------------------------------- shape B: statement

def _read_sections(text: str) -> dict[str, list[tuple[dict[str, str], list[str]]]]:
    """Group ``Section,Header,...`` / ``Section,Data,...`` lines by cleaned section name."""
    headers: dict[str, list[str]] = {}
    out: dict[str, list[tuple[dict[str, str], list[str]]]] = {}
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except Exception:
        return {}
    for row in rows:
        if len(row) < 3:
            continue
        key = clean_header(row[0])
        kind = str(row[1]).strip().lower()
        if not key:
            continue
        if kind == "header":
            headers[key] = _dedupe([str(c).strip() for c in row[2:]])
        elif kind == "data":
            hdr = headers.get(key)
            if not hdr:
                continue
            vals = _fit(row[2:], hdr)
            out.setdefault(key, []).append((dict(zip(hdr, vals)), vals))
    return out


def _is_total(vals: list[str]) -> bool:
    """Skip SubTotal / Total / blank-leading rows."""
    if not vals:
        return True
    first = str(vals[0]).strip().lower()
    if not first or first.startswith("total") or first.startswith("subtotal"):
        return True
    return any(str(v).strip().lower() in {"total", "subtotal"} for v in vals[:2])


def _statement_account_id(sections: dict) -> str:
    for rowdict, _vals in sections.get("accountinformation", []):
        name = clean_header(pick(rowdict, "Field Name", "FieldName"))
        if name in ("account", "accountid"):
            return str(pick(rowdict, "Field Value", "FieldValue") or "").strip()
    return ""


def _parse_statement(sections: dict, account_id: str | None) -> list[dict]:
    acct = _statement_account_id(sections) or (account_id or "")
    out: list[dict] = []
    for rowdict, vals in sections.get("trades", []):
        try:
            txn = _statement_trade(rowdict, vals, acct)
        except Exception:
            continue
        if txn is not None:
            out.append(txn)
    out.extend(_parse_cash_sections(sections, acct))
    return out


_STATEMENT_ASSET = (
    ("option", "option"),
    ("stock", "stock"),
    ("equity", "stock"),
    ("crypto", "crypto"),
    ("forex", "other"),
    ("future", "other"),
    ("bond", "other"),
    ("fund", "stock"),
)


def _statement_asset_type(asset_category: str) -> str:
    c = str(asset_category or "").strip().lower()
    for needle, mapped in _STATEMENT_ASSET:
        if needle in c:
            return mapped
    return "other"


def _statement_trade(row: dict, vals: list[str], account_id: str) -> dict | None:
    if blank_row(row) or _is_total(vals):
        return None
    disc = clean_header(pick(row, "DataDiscriminator"))
    if disc and disc not in ("order", "trade", "execution"):
        return None

    ts = _ts(pick(row, "Date/Time", "DateTime", "Date", "TradeDate"))
    if not ts:
        return None

    asset_type = _statement_asset_type(pick(row, "Asset Category", "AssetCategory", "AssetClass"))
    qty_signed = parse_qty(pick(row, "Quantity"))
    qty = abs(qty_signed)
    side = _side(pick(row, "Buy/Sell", "BuySell"), qty_signed)
    price = abs(parse_money(pick(row, "T. Price", "TPrice", "TradePrice", "Price")))
    mult = 100.0 if asset_type == "option" else 1.0

    raw_symbol = str(pick(row, "Symbol") or "").strip()
    if asset_type == "option":
        symbol, underlying = _parse_option_text(raw_symbol)
    else:
        symbol = raw_symbol.upper()
        underlying = underlying_of(symbol)

    fees = abs(parse_money(pick(row, "Comm/Fee", "CommFee", "Comm in USD", "Commission")))
    amount = _gross_amount(side, qty, price, mult, "", str(pick(row, "Proceeds") or ""))

    external_id = row_hash(*vals)
    return make_txn(
        id="ib:" + external_id[:16],
        source=SOURCE,
        account_id=account_id,
        ts=ts,
        symbol=symbol,
        underlying=underlying,
        asset_type=asset_type,
        side=side,
        open_close=_open_close(pick(row, "Code", "Open/CloseIndicator")),
        qty=qty,
        price=price,
        fees=fees,
        amount=amount,
        description=raw_symbol,
        external_id=external_id,
        raw=row,
    )


# section key -> asset_type for the simple cash sections
_CASH_SECTIONS = {
    "depositswithdrawals": "transfer",
    "depositsandwithdrawals": "transfer",
    "dividends": "dividend",
    "withholdingtax": "fee",
    "interest": "interest",
    "fees": "fee",
    "otherfees": "fee",
}

_CASH_TYPE_MAP = (
    ("dividend", "dividend"),
    ("withholding", "fee"),
    ("tax", "fee"),
    ("interest", "interest"),
    ("deposit", "transfer"),
    ("withdrawal", "transfer"),
    ("transfer", "transfer"),
    ("fee", "fee"),
    ("commission", "fee"),
)


def _cash_type_from_label(label: str) -> str:
    c = str(label or "").strip().lower()
    for needle, mapped in _CASH_TYPE_MAP:
        if needle in c:
            return mapped
    return "other"


def _parse_cash_sections(sections: dict, account_id: str) -> list[dict]:
    """Deposits & Withdrawals / Dividends / Withholding Tax / Interest / Fees."""
    out: list[dict] = []
    for key, asset_type in _CASH_SECTIONS.items():
        for rowdict, vals in sections.get(key, []):
            txn = _cash_row(rowdict, vals, asset_type, account_id)
            if txn is not None:
                out.append(txn)
    for rowdict, vals in sections.get("cashtransactions", []):
        label = pick(rowdict, "Type", "Description")
        txn = _cash_row(rowdict, vals, _cash_type_from_label(label), account_id)
        if txn is not None:
            out.append(txn)
    return out


def _cash_row(row: dict, vals: list[str], asset_type: str, account_id: str) -> dict | None:
    try:
        if blank_row(row) or _is_total(vals):
            return None
        ts = _ts(pick(row, "Date", "Settle Date", "SettleDate", "Date/Time", "Value Date", "Report Date"))
        if not ts:
            return None
        amount = parse_money(pick(row, "Amount", "Proceeds", "Value"))
        desc = str(pick(row, "Description") or "").strip()
        symbol = str(pick(row, "Symbol") or "").strip().upper()
        if not symbol and asset_type in ("dividend", "fee"):
            m = _DIV_SYMBOL.match(desc)
            if m:
                symbol = m.group(1).upper()

        fees = 0.0
        if asset_type == "dividend":
            amount = abs(amount)
        elif asset_type == "fee":
            amount = -abs(amount)
            fees = abs(amount)

        external_id = row_hash(*vals)
        return make_txn(
            id="ib:" + external_id[:16],
            source=SOURCE,
            account_id=account_id,
            ts=ts,
            symbol=symbol if asset_type in ("dividend", "fee") else "",
            underlying=underlying_of(symbol) if asset_type in ("dividend", "fee") else "",
            asset_type=asset_type,
            side="none",
            open_close="",
            qty=0.0,
            price=0.0,
            fees=fees,
            amount=amount,
            description=desc or str(pick(row, "Type") or "").strip(),
            external_id=external_id,
            raw=row,
        )
    except Exception:
        return None
