"""Monthly account-summary CSV ("statement") ingestion.

The Money-Ops track feeds an agent *monthly account summaries* alongside
transaction-level data. A summary file is one row per month carrying an equity
(or net liquidation / ending balance) figure, optionally cash and the month's
cash-flow lines.

Each row becomes a `snapshots` row so `PeriodSummary.ending_equity` /
`ending_cash` are populated, plus one transaction per cash-flow column present
(deposits / withdrawals / fees / dividends / interest) so the period summaries
pick those up too.
"""
from __future__ import annotations

import calendar
import csv
import io
import re
from typing import Any

from app.ingest.normalize import (blank_row, clean_header, make_txn, parse_money, pick, row_hash,
                                  strip_bom, to_iso_date)

SOURCE = "account_summary"

PERIOD_COLS = ("period", "month", "as_of", "as of", "date", "statement_date", "statement date",
               "period_end", "period end", "month_end")
EQUITY_COLS = ("equity", "net_liquidation", "net liquidation", "netliquidation", "ending_balance",
               "ending balance", "portfolio_value", "portfolio value", "total_value", "total value")
CASH_COLS = ("cash", "ending_cash", "ending cash", "cash_balance", "cash balance",
             "total_cash", "settled_cash")
ACCOUNT_COLS = ("account_id", "account", "accountid", "clientaccountid", "account_number")

# cash-flow column -> (asset_type, sign applied to |value|)
FLOW_COLS: dict[str, tuple[str, ...]] = {
    "deposits": ("deposit", "deposits", "contributions", "deposits_in", "cash_in"),
    "withdrawals": ("withdrawals", "withdrawal", "distributions", "cash_out"),
    "net_deposits": ("net_deposits", "net deposits", "net_transfers", "net_cash_flow_transfers"),
    "fees": ("fees", "fee", "commissions", "total_fees"),
    "dividends": ("dividends", "dividend", "dividend_income"),
    "interest": ("interest", "interest_income", "interest_earned"),
}


def _has(cols: set[str], names: tuple[str, ...]) -> bool:
    return any(clean_header(n) in cols for n in names)


def looks_like_account_summary(text: str) -> bool:
    """True when the header carries an equity-like column AND a period/date column."""
    try:
        cleaned = strip_bom(text or "")
        if not cleaned.strip():
            return False
        header_line = next((ln for ln in cleaned.splitlines() if ln.strip()), "")
        try:
            cols = {clean_header(c) for c in next(csv.reader(io.StringIO(header_line)))}
        except Exception:
            cols = {clean_header(c) for c in header_line.split(",")}
        return _has(cols, EQUITY_COLS) and _has(cols, PERIOD_COLS)
    except Exception:
        return False


def _as_of(value: Any) -> str | None:
    """'2026-07' -> last day of July; a full date -> that date. Time is 20:00 so the
    snapshot sorts after the month's transactions."""
    s = str(value or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})", s)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            return None
        return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}T20:00:00"
    m = re.fullmatch(r"(\d{1,2})[-/](\d{4})", s)          # 07/2026
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}T20:00:00"
    iso = to_iso_date(s)
    return f"{iso}T20:00:00" if iso else None


def parse_account_summary_csv(text: str, account_id: str = "statement",
                              ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse a monthly account-summary CSV.

    Returns ``(snapshots, transactions)``. Never raises on a malformed row — it is
    skipped. Raises ValueError only when the file has no usable header at all.
    """
    cleaned = strip_bom(text or "")
    if not cleaned.strip():
        return [], []

    reader = csv.DictReader(io.StringIO(cleaned))
    if not reader.fieldnames:
        raise ValueError("not an account-summary CSV (no header row)")
    cols = {clean_header(c) for c in reader.fieldnames if c}
    if not (_has(cols, EQUITY_COLS) and _has(cols, PERIOD_COLS)):
        raise ValueError("not an account-summary CSV (need an equity column and a period column)")

    snapshots: list[dict[str, Any]] = []
    txns: list[dict[str, Any]] = []

    for row in reader:
        try:
            if blank_row(row):
                continue
            as_of = _as_of(pick(row, *PERIOD_COLS))
            if not as_of:
                continue
            equity_raw = pick(row, *EQUITY_COLS)
            if str(equity_raw).strip() == "":
                continue
            equity = parse_money(equity_raw)
            acct = str(pick(row, *ACCOUNT_COLS) or account_id).strip() or account_id

            cash_raw = pick(row, *CASH_COLS)
            cash = parse_money(cash_raw) if str(cash_raw).strip() != "" else None

            digest = row_hash(as_of, acct, *(str(v) for v in row.values()))
            snapshots.append({
                "id": "sum:" + digest[:16],
                "source": SOURCE,
                "account_id": acct,
                "as_of": as_of,
                "equity": equity,
                "cash": cash,
                "positions_json": "[]",
            })

            for kind, names in FLOW_COLS.items():
                raw = pick(row, *names)
                if str(raw).strip() == "":
                    continue
                value = parse_money(raw)
                if value == 0:
                    continue
                if kind == "deposits":
                    asset_type, amount, desc = "transfer", abs(value), "Deposits (statement)"
                elif kind == "withdrawals":
                    asset_type, amount, desc = "transfer", -abs(value), "Withdrawals (statement)"
                elif kind == "net_deposits":
                    if any(clean_header(n) in cols for n in FLOW_COLS["deposits"]):
                        continue                      # already covered by deposits/withdrawals
                    asset_type, amount, desc = "transfer", value, "Net deposits (statement)"
                elif kind == "fees":
                    asset_type, amount, desc = "fee", -abs(value), "Fees (statement)"
                elif kind == "dividends":
                    asset_type, amount, desc = "dividend", abs(value), "Dividends (statement)"
                else:
                    asset_type, amount, desc = "interest", abs(value), "Interest (statement)"

                txns.append(make_txn(
                    id="sum:" + row_hash(digest, kind)[:16],
                    source=SOURCE, account_id=acct, ts=as_of, asset_type=asset_type,
                    side="none", amount=amount,
                    fees=abs(value) if asset_type == "fee" else 0.0,
                    description=desc, external_id=digest,
                    raw={k: v for k, v in row.items() if k},
                ))
        except Exception:
            continue

    snapshots.sort(key=lambda s: s["as_of"])
    txns.sort(key=lambda t: t["ts"])
    return snapshots, txns
