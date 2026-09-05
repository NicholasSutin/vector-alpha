"""Insight memory — what the agent learned in previous runs (`insights` table)."""
from __future__ import annotations

import json
from typing import Any

from app.db import get_conn, new_id, now_iso

VALID_STATUS = {"open", "followed", "ignored", "validated", "invalidated"}


def _row_to_insight(r: Any) -> dict[str, Any]:
    d = dict(r)
    try:
        d["evidence"] = json.loads(d.pop("evidence_json", "[]") or "[]")
    except Exception:
        d["evidence"] = []
    if not isinstance(d["evidence"], list):
        d["evidence"] = []
    return d


def save_insight(run_id: str | None, kind: str, text: str, evidence: list[str] | None = None) -> dict[str, Any]:
    """kind ∈ observation|advisement|prediction."""
    iid = new_id("i_")
    created = now_iso()
    ev = [str(e) for e in (evidence or [])][:20]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO insights (id, run_id, created_at, kind, text, evidence_json, status) VALUES (?,?,?,?,?,?,?)",
            (iid, run_id, created, kind, text, json.dumps(ev), "open"),
        )
    return {
        "id": iid,
        "run_id": run_id,
        "created_at": created,
        "kind": kind,
        "text": text,
        "evidence": ev,
        "status": "open",
    }


def recall_insights(limit: int = 20) -> list[dict[str, Any]]:
    """Newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM insights ORDER BY created_at DESC, id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [_row_to_insight(r) for r in rows]


def list_all() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM insights ORDER BY created_at DESC, id DESC").fetchall()
    return [_row_to_insight(r) for r in rows]


def get_insight(insight_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM insights WHERE id=?", (insight_id,)).fetchone()
    return _row_to_insight(r) if r else None


def update_status(insight_id: str, status: str) -> dict[str, Any] | None:
    if status not in VALID_STATUS:
        raise ValueError(f"invalid status {status!r}; expected one of {sorted(VALID_STATUS)}")
    with get_conn() as conn:
        conn.execute("UPDATE insights SET status=? WHERE id=?", (status, insight_id))
    return get_insight(insight_id)
