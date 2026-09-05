"""SQLite access. One connection per call (check_same_thread off); WAL mode.

Usage:
    from app.db import get_conn, init_db, insert_transactions
    with get_conn() as conn: ...
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  account_id TEXT NOT NULL DEFAULT '',
  ts TEXT NOT NULL,
  symbol TEXT NOT NULL DEFAULT '',
  underlying TEXT NOT NULL DEFAULT '',
  asset_type TEXT NOT NULL DEFAULT 'other',
  side TEXT NOT NULL DEFAULT 'none',
  open_close TEXT NOT NULL DEFAULT '',
  qty REAL NOT NULL DEFAULT 0,
  price REAL NOT NULL DEFAULT 0,
  fees REAL NOT NULL DEFAULT 0,
  amount REAL NOT NULL DEFAULT 0,
  description TEXT NOT NULL DEFAULT '',
  external_id TEXT NOT NULL DEFAULT '',
  strategy_tag TEXT NOT NULL DEFAULT '',
  raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_txn_ts ON transactions(ts);
CREATE INDEX IF NOT EXISTS ix_txn_symbol ON transactions(symbol);
CREATE INDEX IF NOT EXISTS ix_txn_source ON transactions(source);

CREATE TABLE IF NOT EXISTS snapshots (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  account_id TEXT NOT NULL DEFAULT '',
  as_of TEXT NOT NULL,
  equity REAL,
  cash REAL,
  positions_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  period_a TEXT NOT NULL,
  period_b TEXT NOT NULL,
  question TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  report_json TEXT,
  events_json TEXT NOT NULL DEFAULT '[]',
  prism_session_id TEXT,
  model TEXT,
  latency_ms INTEGER,
  error TEXT
);

CREATE TABLE IF NOT EXISTS insights (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  created_at TEXT NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  run_id TEXT,
  account_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  conid INTEGER,
  side TEXT NOT NULL,
  qty REAL NOT NULL,
  order_type TEXT NOT NULL,
  limit_price REAL,
  tif TEXT NOT NULL DEFAULT 'DAY',
  status TEXT NOT NULL DEFAULT 'proposed',
  broker_order_id TEXT,
  preview_json TEXT,
  result_json TEXT,
  rationale TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(settings.db_path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# ---------- transactions ----------
TXN_COLS = ["id", "source", "account_id", "ts", "symbol", "underlying", "asset_type", "side", "open_close",
            "qty", "price", "fees", "amount", "description", "external_id", "strategy_tag", "raw_json"]


def insert_transactions(txns: list[dict[str, Any]], replace_source: str | None = None) -> tuple[int, int]:
    """Insert normalized transactions. Returns (inserted, skipped_duplicates).
    If replace_source is given, rows from that source are deleted first (idempotent reloads)."""
    inserted = skipped = 0
    with get_conn() as conn:
        if replace_source:
            conn.execute("DELETE FROM transactions WHERE source=?", (replace_source,))
        for t in txns:
            row = {c: t.get(c, "") for c in TXN_COLS}
            if not row["id"]:
                row["id"] = new_id("t_")
            if isinstance(row["raw_json"], (dict, list)):
                row["raw_json"] = json.dumps(row["raw_json"], default=str)
            for c in ("qty", "price", "fees", "amount"):
                row[c] = float(row[c] or 0)
            cur = conn.execute(
                f"INSERT OR IGNORE INTO transactions ({','.join(TXN_COLS)}) VALUES ({','.join('?' for _ in TXN_COLS)})",
                [row[c] for c in TXN_COLS],
            )
            if cur.rowcount:
                inserted += 1
            else:
                skipped += 1
    return inserted, skipped


def fetch_transactions(where: str = "", params: tuple = (), limit: int | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM transactions"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY ts ASC, id ASC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with get_conn() as conn:
        return rows_to_dicts(conn.execute(sql, params).fetchall())


# ---------- generic helpers ----------
def kv_get(key: str, default: str | None = None) -> str | None:
    with get_conn() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def kv_set(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def reset_all() -> None:
    with get_conn() as conn:
        for t in ("transactions", "snapshots", "runs", "insights", "orders"):
            conn.execute(f"DELETE FROM {t}")
