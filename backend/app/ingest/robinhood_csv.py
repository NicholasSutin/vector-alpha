"""Robinhood "Account activity" CSV -> normalized transaction dicts.

Pure stdlib. Tolerates a UTF-8 BOM, arbitrary column order, extra columns
(``Account Type`` / ``Suppressed``), preamble lines and the footer/disclaimer
block Robinhood appends to its exports.

The only thing that raises is a *wrong file*: a header row without an
``Activity Date`` column raises ``ValueError``. Malformed **data rows** are
always skipped silently, and an unknown ``Trans Code`` is kept as
``asset_type="other"`` rather than dropped.
"""
from __future__ import annotations

import csv
import io
from typing import Any

from .normalize import (
    blank_row,
    clean_header,
    make_txn,
    option_symbol,  # noqa: F401  (re-exported convenience)
    parse_date,
    parse_money,
    parse_option_description,
    parse_qty,
    pick,
    row_hash,
    strip_bom,
    underlying_of,
)

SOURCE = "robinhood_csv"

# Cleaned header names seen in Robinhood activity exports.
_KNOWN_HEADERS = {
    "activitydate", "processdate", "settledate", "instrument", "description",
    "transcode", "quantity", "price", "amount", "accounttype", "suppressed",
}

# Trans Code -> (asset_type, side, open_close)
_TRANS_CODES: dict[str, tuple[str, str, str]] = {
    # equity trades
    "BUY": ("stock", "buy", "open"),
    "SELL": ("stock", "sell", "close"),
    # option trades
    "BTO": ("option", "buy", "open"),
    "STC": ("option", "sell", "close"),
    "STO": ("option", "sell", "open"),
    "BTC": ("option", "buy", "close"),
    # option lifecycle (all close at price 0)
    "OEXP": ("option", "none", "close"),
    "OASGN": ("option", "none", "close"),
    "OEXCS": ("option", "none", "close"),
    "OEXC": ("option", "none", "close"),
    # cash events
    "CDIV": ("dividend", "none", ""),
    "ACH": ("transfer", "none", ""),
    "RTP": ("transfer", "none", ""),
    "WIRE": ("transfer", "none", ""),
    "INT": ("interest", "none", ""),
    "SLIP": ("interest", "none", ""),   # stock-lending income
    # fees / taxes
    "GOLD": ("fee", "none", ""),
    "AFEE": ("fee", "none", ""),
    "DFEE": ("fee", "none", ""),
    "MISC": ("fee", "none", ""),
    "DTAX": ("fee", "none", ""),
    # corporate actions
    "CONV": ("other", "none", ""),
    "MRGS": ("other", "none", ""),
    "SXCH": ("other", "none", ""),
    "SPL": ("other", "none", ""),
}

_OPTION_CODES = {"BTO", "STC", "STO", "BTC", "OEXP", "OASGN", "OEXCS", "OEXC"}
_ZERO_PRICE_CODES = {"OEXP", "OASGN", "OEXCS", "OEXC"}
_ASSIGN_NOTE = {"OASGN": "assignment", "OEXCS": "exercise", "OEXC": "exercise"}

# Prefixes Robinhood puts in front of an option description on lifecycle rows.
_DESC_PREFIXES = (
    "option expiration for ",
    "option assignment for ",
    "option exercise for ",
    "option expiration ",
    "option assignment ",
    "option exercise ",
)

# Footer / disclaimer fragments (matched case-insensitively against the joined row).
_FOOTER_PHRASES = (
    "robinhood securities",
    "robinhood financial",
    "member sipc",
    "the data provided",
    "page ",
)

# Placeholder glyphs Robinhood uses for "not applicable".
_DASHES = {"—", "–", "-", "--", ""}


# ---------------------------------------------------------------- csv reading

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


def _read_table(text: str) -> tuple[list[str], list[list[str]]]:
    """Split raw CSV text into (header, data rows), skipping any preamble.

    Raises ``ValueError`` when a non-empty document has no ``Activity Date``
    header — that means the caller handed us the wrong file.
    """
    cleaned_text = strip_bom(text or "")
    if not cleaned_text.strip():
        return [], []
    try:
        rows = [r for r in csv.reader(io.StringIO(cleaned_text))]
    except Exception:
        return [], []
    header_idx = -1
    for i, row in enumerate(rows[:100]):
        cols = {clean_header(c) for c in row}
        if "activitydate" in cols and len(cols & _KNOWN_HEADERS) >= 2:
            header_idx = i
            break
    if header_idx < 0:
        raise ValueError("not a Robinhood activity export")
    header = _dedupe([str(c).strip() for c in rows[header_idx]])
    return header, rows[header_idx + 1:]


def _num(value: Any) -> str:
    """Blank out em/en-dash placeholders before numeric parsing."""
    s = str(value or "").strip()
    return "" if s in _DASHES else s


def _is_footer(cells: list[str], row: dict[str, Any]) -> bool:
    """Heuristics for Robinhood's trailing disclaimer / subtotal block."""
    non_empty = [str(c).strip() for c in cells if str(c).strip()]
    if len(non_empty) < 3:
        return True
    joined = " ".join(non_empty).lower()
    if any(p in joined for p in _FOOTER_PHRASES):
        return True
    if non_empty[0].lower().startswith("total"):
        return True
    if any(str(c).strip().lower() in {"total", "totals", "subtotal"} for c in cells):
        return True
    activity = str(pick(row, "Activity Date", "ActivityDate", "Date") or "").strip()
    code = str(pick(row, "Trans Code", "TransCode", "Code") or "").strip()
    return not activity and not code


def _strip_desc_prefix(desc: str) -> str:
    """'Option Expiration for LULU 4/16/2026 Call $310.00' -> 'LULU 4/16/2026 ...'."""
    s = (desc or "").strip()
    low = s.lower()
    for p in _DESC_PREFIXES:
        if low.startswith(p):
            return s[len(p):].strip()
    return s


# ---------------------------------------------------------------- main entry

def parse_robinhood_activity_csv(text: str, account_id: str = "robinhood") -> list[dict]:
    """Parse a Robinhood account-activity CSV into normalized transaction dicts.

    Returns ``[]`` for empty input. Raises ``ValueError`` if the header row has
    no ``Activity Date`` column. Individual bad rows are skipped, not fatal.
    Result is sorted by ``ts`` ascending (exports arrive reverse-chronological).
    """
    header, data_rows = _read_table(text)
    if not header:
        return []

    out: list[dict] = []
    for cells in data_rows:
        try:
            txn = _row_to_txn(header, cells, account_id)
        except Exception:
            continue
        if txn is not None:
            out.append(txn)

    # Exports are newest-first; flip so a stable sort keeps intra-day order sane.
    if len(out) > 1 and (out[0].get("ts") or "") > (out[-1].get("ts") or ""):
        out.reverse()
    out.sort(key=lambda t: t.get("ts") or "")
    return out


def _row_to_txn(header: list[str], cells: list[str], account_id: str) -> dict | None:
    """Convert one raw CSV row into a transaction dict (or None to skip it)."""
    padded = [str(c) if c is not None else "" for c in cells]
    if len(padded) < len(header):
        padded += [""] * (len(header) - len(padded))
    elif len(padded) > len(header):
        padded = padded[:len(header)]
    row = dict(zip(header, padded))
    if blank_row(row) or _is_footer(padded, row):
        return None

    ts = parse_date(pick(row, "Activity Date", "ActivityDate", "Date"))
    if not ts:
        return None

    code = str(pick(row, "Trans Code", "TransCode", "Code") or "").strip().upper()
    instrument = str(pick(row, "Instrument", "Symbol") or "").strip().upper()
    desc = str(pick(row, "Description") or "").strip()
    qty = abs(parse_qty(_num(pick(row, "Quantity", "Qty", "Shares"))))
    price = parse_money(_num(pick(row, "Price")))
    amount_raw = _num(pick(row, "Amount"))
    amount_given = amount_raw != ""
    amount = parse_money(amount_raw)

    asset_type, side, open_close = _TRANS_CODES.get(code, ("other", "none", ""))

    symbol = instrument
    underlying = instrument
    if code in _OPTION_CODES:
        parsed = parse_option_description(_strip_desc_prefix(desc), fallback_underlying=instrument)
        if parsed:
            symbol = parsed["symbol"]
            underlying = instrument or parsed["underlying"]
        else:
            # Best effort: keep the instrument as the symbol, still an option row.
            symbol = instrument
            underlying = instrument

    mult = 100.0 if asset_type == "option" else 1.0

    if code in _ZERO_PRICE_CODES:
        price = 0.0
        amount = 0.0 if code == "OEXP" or not amount_given else amount
        note = _ASSIGN_NOTE.get(code)
        if note:
            desc = f"{desc} ({note})" if desc else note
    elif not amount_given:
        gross = qty * price * mult
        amount = -gross if side == "buy" else (gross if side == "sell" else 0.0)

    fees = 0.0
    if asset_type == "fee":
        fees = abs(amount)
        amount = -abs(amount)
    elif asset_type == "dividend":
        amount = abs(amount)

    external_id = row_hash(*padded)
    return make_txn(
        id="rh:" + external_id[:16],
        source=SOURCE,
        account_id=account_id or "",
        ts=ts,
        symbol=symbol,
        underlying=underlying or underlying_of(symbol),
        asset_type=asset_type,
        side=side,
        open_close=open_close,
        qty=qty,
        price=price,
        fees=fees,
        amount=amount,
        description=desc,
        external_id=external_id,
        raw=row,
    )
