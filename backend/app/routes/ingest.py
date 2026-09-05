"""CSV / demo ingestion endpoints (docs/API.md, "Ingest")."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.analytics import service
from app.db import get_conn, init_db, reset_all
from app.ingest import generic_csv, ibkr_flex_csv, robinhood_csv, synthetic
from app.ingest.normalize import strip_bom, summarize_ingest
from app.models import IngestResult

log = logging.getLogger("vector-alpha.ingest")
router = APIRouter(tags=["ingest"])

_PARSERS = {
    "robinhood": ("robinhood_csv", lambda text: robinhood_csv.parse_robinhood_activity_csv(text)),
    "ibkr_flex": ("ibkr_flex", lambda text: ibkr_flex_csv.parse_ibkr_flex_csv(text)),
    "generic": ("generic_csv", lambda text: generic_csv.parse_generic_csv(text)),
}


def _result(source: str, txns: list[dict[str, Any]], inserted: int, skipped: int,
            warnings: list[str]) -> IngestResult:
    meta = summarize_ingest(txns)
    return IngestResult(ok=True, source=source, inserted=inserted, skipped_duplicates=skipped,
                        warnings=warnings, **meta)


def _insert(txns: list[dict[str, Any]], replace_source: str | None = None) -> tuple[int, int]:
    from app.db import insert_transactions
    init_db()
    out = insert_transactions(txns, replace_source=replace_source)
    service.invalidate_cache()
    return out


@router.post("/ingest/csv", response_model=IngestResult)
async def ingest_csv(file: UploadFile = File(...), source: Optional[str] = Form(None)) -> IngestResult:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")
    text = strip_bom(raw.decode("utf-8", errors="replace"))

    chosen = (source or "").strip().lower()
    warnings: list[str] = []
    if chosen not in _PARSERS:
        if chosen:
            warnings.append(f"unknown source '{chosen}', auto-detecting instead")
        chosen = generic_csv.detect_source(text)
        warnings.append(f"auto-detected source: {chosen}")

    src_name, parse = _PARSERS[chosen]
    try:
        txns = parse(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse as {chosen}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - never 500 on a bad upload
        log.exception("csv parse failed")
        raise HTTPException(status_code=400, detail=f"Could not parse CSV: {exc}") from exc

    if not txns:
        raise HTTPException(status_code=400, detail=f"No usable rows found (parsed as {chosen}).")

    inserted, skipped = _insert(txns)
    if skipped:
        warnings.append(f"{skipped} duplicate rows skipped")
    return _result(src_name, txns, inserted, skipped, warnings)


@router.post("/ingest/demo", response_model=IngestResult)
def ingest_demo() -> IngestResult:
    """Load the deterministic synthetic book (idempotent: replaces prior synthetic rows)."""
    txns = synthetic.generate_synthetic_book()
    inserted, skipped = _insert(txns, replace_source=synthetic.SOURCE)

    snaps = synthetic.generate_synthetic_snapshots()
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM snapshots WHERE source=?", (synthetic.SOURCE,))
            for s in snaps:
                conn.execute(
                    "INSERT OR REPLACE INTO snapshots (id, source, account_id, as_of, equity, cash,"
                    " positions_json) VALUES (?,?,?,?,?,?,?)",
                    (s["id"], s["source"], s["account_id"], s["as_of"], s["equity"], s["cash"],
                     s["positions_json"]),
                )
        service.invalidate_cache()
    except Exception as exc:  # noqa: BLE001 - snapshots are a nice-to-have
        log.warning("snapshot insert failed: %s", exc)

    return _result(synthetic.SOURCE, txns, inserted, skipped,
                   [f"{len(snaps)} month-end snapshots loaded"])


@router.post("/ingest/reset")
def ingest_reset() -> dict:
    init_db()
    reset_all()
    service.invalidate_cache()
    return {"ok": True}
