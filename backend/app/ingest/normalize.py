"""Normalization helpers shared by every CSV parser.

Pure stdlib. No broker specifics live here beyond generic money/date/symbol shapes.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable

# ---------------------------------------------------------------- constants

TRADE_ASSET_TYPES = ("stock", "option", "crypto")
ASSET_TYPES = ("stock", "option", "crypto", "dividend", "interest", "fee", "transfer", "other")

_MONEY_RE = re.compile(r"[^0-9.\-]")


# ---------------------------------------------------------------- money

def parse_money(value: Any) -> float:
    """Parse '$1,234.56', '(1,234.56)' (negative), '-1,234.56', '' -> float.

    Returns 0.0 for anything unparseable.
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s or s in {"-", "--", "n/a", "N/A", "NaN", "nan"}:
        return 0.0
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace("−", "-")  # unicode minus
    s = _MONEY_RE.sub("", s)
    if s in {"", "-", ".", "-."}:
        return 0.0
    # a stray trailing/inner extra minus
    if s.count("-") > 1 or (s.rfind("-") > 0):
        s = ("-" if s.startswith("-") else "") + s.replace("-", "")
    try:
        out = float(s)
    except ValueError:
        return 0.0
    return -out if neg else out


def parse_qty(value: Any) -> float:
    """Quantity parser: tolerates 'S' suffixes, commas, blanks. Sign preserved."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0
    s = s.replace(",", "").rstrip("S").strip()
    return parse_money(s)


# ---------------------------------------------------------------- dates

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%Y%m%d;%H%M%S",
    "%Y%m%d %H%M%S",
    "%Y%m%d",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d/%m/%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d-%b-%y",
    "%d-%b-%Y",
)


def parse_date(value: Any, *, default_time: str = "00:00:00") -> str | None:
    """Parse many broker date formats -> ISO 'YYYY-MM-DDTHH:MM:SS'. None if unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(timespec="seconds")
    if isinstance(value, date):
        return f"{value.isoformat()}T{default_time}"
    s = str(value).strip().strip('"')
    if not s:
        return None
    s = s.replace(",", " ") if re.match(r"^\d{8};", s) is None and "," in s and "/" not in s and "-" not in s else s
    s = re.sub(r"\s+", " ", s).strip()
    # ISO with timezone
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.isoformat(timespec="seconds")
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%Y%m%d",
                   "%b %d, %Y", "%B %d, %Y", "%d-%b-%y", "%d-%b-%Y"):
            return f"{dt.date().isoformat()}T{default_time}"
        return dt.isoformat(timespec="seconds")
    return None


def to_iso_date(value: Any) -> str | None:
    """Parse to a plain 'YYYY-MM-DD' date string."""
    iso = parse_date(value)
    return iso[:10] if iso else None


def period_of(ts: str) -> str:
    """'2026-07-14T10:31:00' -> '2026-07'."""
    return (ts or "")[:7]


# ---------------------------------------------------------------- symbols

_CP_MAP = {
    "C": "C", "CALL": "C", "P": "P", "PUT": "P",
    "c": "C", "call": "C", "p": "P", "put": "P",
}


def fmt_strike(strike: Any) -> str:
    """140.0 -> '140'; 140.5 -> '140.5'; '$140.00' -> '140'."""
    v = parse_money(strike) if not isinstance(strike, (int, float)) else float(strike)
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def option_symbol(underlying: str, expiry_date: Any, cp: str, strike: Any) -> str:
    """Canonical option symbol: 'NVDA 2026-06-20 C 140'."""
    und = (underlying or "").strip().upper()
    exp = to_iso_date(expiry_date) or str(expiry_date or "").strip()
    call_put = _CP_MAP.get(str(cp or "").strip(), str(cp or "").strip().upper()[:1] or "C")
    return f"{und} {exp} {call_put} {fmt_strike(strike)}"


# "NVDA 6/20/2025 Call $140.00"  /  "NVDA 6/20/2025 Put $140"
_RH_OPTION_DESC = re.compile(
    r"^\s*(?P<und>[A-Z\.]{1,6})\s+"
    r"(?P<exp>\d{1,2}/\d{1,2}/\d{2,4})\s+"
    r"(?P<cp>Call|Put|CALL|PUT|call|put)\s+"
    r"\$?\s*(?P<strike>[0-9,]+(?:\.\d+)?)",
)


def parse_option_description(desc: str, fallback_underlying: str = "") -> dict[str, Any] | None:
    """Parse a Robinhood-style option description into its parts, or None."""
    if not desc:
        return None
    m = _RH_OPTION_DESC.search(desc)
    if not m:
        return None
    und = (m.group("und") or fallback_underlying or "").upper()
    return {
        "underlying": und,
        "expiry": to_iso_date(m.group("exp")),
        "cp": _CP_MAP.get(m.group("cp"), "C"),
        "strike": parse_money(m.group("strike")),
        "symbol": option_symbol(und, m.group("exp"), m.group("cp"), m.group("strike")),
    }


def parse_symbol_asset_type(symbol: str) -> str:
    """Guess asset type from a canonical symbol string."""
    s = (symbol or "").strip()
    if len(s.split()) >= 4:
        return "option"
    if s.upper() in {"BTC", "ETH", "DOGE", "SOL", "LTC", "BCH", "ETC", "AVAX", "XRP", "ADA",
                     "BTC-USD", "ETH-USD"}:
        return "crypto"
    return "stock"


def underlying_of(symbol: str) -> str:
    """'NVDA 2026-06-20 C 140' -> 'NVDA'; 'AAPL' -> 'AAPL'."""
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    return s.split()[0].split("-")[0]


# ---------------------------------------------------------------- ids / rows

def row_hash(*parts: Any) -> str:
    payload = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()


def strip_bom(text: str) -> str:
    return text.lstrip("﻿") if text else (text or "")


def clean_header(name: str) -> str:
    """Normalize a CSV header cell for tolerant lookup: lowercase, alnum only."""
    return re.sub(r"[^a-z0-9]", "", (name or "").strip().lower())


def pick(row: dict[str, Any], *names: str, default: Any = "") -> Any:
    """Tolerant column lookup: case/space/punctuation insensitive, first hit wins."""
    if not row:
        return default
    norm = {clean_header(k): v for k, v in row.items() if k is not None}
    for n in names:
        key = clean_header(n)
        if key in norm and norm[key] not in (None, ""):
            return norm[key]
    for n in names:
        key = clean_header(n)
        if key in norm:
            return norm[key]
    return default


def blank_row(row: dict[str, Any] | None) -> bool:
    if not row:
        return True
    return all((v is None or str(v).strip() == "") for v in row.values())


def make_txn(
    *,
    id: str,
    source: str,
    account_id: str = "",
    ts: str,
    symbol: str = "",
    underlying: str = "",
    asset_type: str = "other",
    side: str = "none",
    open_close: str = "",
    qty: float = 0.0,
    price: float = 0.0,
    fees: float = 0.0,
    amount: float = 0.0,
    description: str = "",
    external_id: str = "",
    strategy_tag: str = "",
    raw: Any = None,
) -> dict[str, Any]:
    """Build a normalized transaction dict matching the `transactions` table."""
    return {
        "id": id,
        "source": source,
        "account_id": account_id or "",
        "ts": ts,
        "symbol": symbol or "",
        "underlying": (underlying or underlying_of(symbol)).upper(),
        "asset_type": asset_type,
        "side": side or "none",
        "open_close": open_close or "",
        "qty": float(qty or 0),
        "price": float(price or 0),
        "fees": abs(float(fees or 0)),
        "amount": float(amount or 0),
        "description": description or "",
        "external_id": external_id or "",
        "strategy_tag": strategy_tag or "",
        "raw_json": raw if raw is not None else {},
    }


def summarize_ingest(txns: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """account_ids / date_range / asset_types counts for an IngestResult."""
    txns = list(txns)
    accounts = sorted({t.get("account_id", "") for t in txns if t.get("account_id")})
    tss = sorted(t.get("ts", "") for t in txns if t.get("ts"))
    counts: dict[str, int] = {}
    for t in txns:
        k = t.get("asset_type", "other")
        counts[k] = counts.get(k, 0) + 1
    return {
        "account_ids": accounts,
        "date_range": {"start": tss[0][:10] if tss else None, "end": tss[-1][:10] if tss else None},
        "asset_types": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
    }
