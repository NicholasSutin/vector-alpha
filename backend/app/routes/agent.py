"""Agent HTTP surface: /analyze, SSE events, runs, insights, prism status."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.agent import memory
from app.agent.events import bus
from app.agent.runner import run_analysis
from app.db import get_conn, new_id, now_iso
from app.integrations.prism import prism

log = logging.getLogger("vector-alpha.routes.agent")

router = APIRouter(tags=["agent"])


class AnalyzeBody(BaseModel):
    a: Optional[str] = None
    b: Optional[str] = None
    question: Optional[str] = None


class InsightPatch(BaseModel):
    status: str


def _run_row(r: Any) -> dict[str, Any]:
    d = dict(r)
    headline = None
    if d.get("report_json"):
        try:
            headline = (json.loads(d["report_json"]) or {}).get("headline")
        except Exception:
            headline = None
    return {
        "id": d["id"],
        "created_at": d["created_at"],
        "period_a": d["period_a"],
        "period_b": d["period_b"],
        "status": d["status"],
        "headline": headline,
        "model": d.get("model"),
        "latency_ms": d.get("latency_ms"),
        "prism_session_id": d.get("prism_session_id"),
    }


@router.post("/analyze")
async def analyze(body: AnalyzeBody) -> dict[str, Any]:
    a, b = body.a, body.b
    if not (a and b):
        try:
            from app.analytics import service as analytics

            pair = analytics.latest_two_periods()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"analytics unavailable: {e}")
        if not pair:
            raise HTTPException(status_code=400, detail="not enough data: need two periods (load a book first)")
        a, b = pair

    run_id = new_id("r_")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, created_at, period_a, period_b, question, status, prism_session_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, now_iso(), a, b, body.question or "", "running", run_id),
        )
    bus.publish(run_id, {"type": "status", "message": f"Run queued for {a} → {b}"})
    asyncio.create_task(run_analysis(run_id, a, b, body.question))
    return {"run_id": run_id, "a": a, "b": b}


@router.get("/analyze/{run_id}/events")
async def analyze_events(run_id: str):
    async def gen():
        async for ev in bus.subscribe(run_id):
            yield {"data": json.dumps(ev, default=str)}

    return EventSourceResponse(
        gen(),
        ping=15,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC, id DESC LIMIT 100").fetchall()
    return {"runs": [_run_row(r) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="run not found")
    d = dict(r)
    report = None
    if d.get("report_json"):
        try:
            report = json.loads(d["report_json"])
        except Exception:
            report = None
    events = bus.events(run_id)
    if not events:
        try:
            events = json.loads(d.get("events_json") or "[]")
        except Exception:
            events = []
    return {"run": _run_row(r), "report": report, "events": events, "error": d.get("error")}


@router.get("/insights")
def list_insights() -> dict[str, Any]:
    return {"insights": memory.list_all()}


@router.patch("/insights/{insight_id}")
def patch_insight(insight_id: str, body: InsightPatch) -> dict[str, Any]:
    try:
        updated = memory.update_status(insight_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not updated:
        raise HTTPException(status_code=404, detail="insight not found")
    return updated


@router.get("/prism/status")
def prism_status() -> dict[str, Any]:
    return prism.doctor()
