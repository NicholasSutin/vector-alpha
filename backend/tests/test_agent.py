"""End-to-end tests for the reasoning agent (no network: PRISM/LLM/Tavily all disabled)."""
from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

from tests.fixtures_analytics import COMPARE, POSITIONS, make_fake_service


@pytest.fixture
def fake_analytics(monkeypatch):
    """Install a fake `app.analytics.service` module before the runner imports it."""
    fake = make_fake_service()
    pkg = sys.modules.get("app.analytics")
    if pkg is None:
        pkg = types.ModuleType("app.analytics")
        pkg.__path__ = []  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "app.analytics", pkg)
    monkeypatch.setitem(sys.modules, "app.analytics.service", fake)
    monkeypatch.setattr(pkg, "service", fake, raising=False)
    return fake


# --------------------------------------------------------------------------- runner
def test_run_analysis_end_to_end(fake_analytics):
    from app.agent import memory
    from app.agent.events import bus
    from app.agent.runner import run_analysis
    from app.db import get_conn, now_iso

    run_id = "r_test1"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, created_at, period_a, period_b, question, status) VALUES (?,?,?,?,?,?)",
            (run_id, now_iso(), "2026-06", "2026-07", "why did P&L fall?", "running"),
        )

    report = asyncio.run(run_analysis(run_id, "2026-06", "2026-07", "why did P&L fall?"))

    events = bus.events(run_id)
    kinds = [e["type"] for e in events]
    assert "status" in kinds and "tool_call" in kinds and "tool_result" in kinds
    finals = [e for e in events if e["type"] == "final"]
    assert len(finals) == 1, kinds
    final = finals[0]
    assert final["fallback"] is True                    # no LLM configured in tests
    assert final["prism"]["session_id"] == run_id
    assert final["prism"]["traced"] is False            # PRISM disabled in tests
    assert final["model"] == "fallback-deterministic"
    assert isinstance(final["latency_ms"], int)

    # the tools actually ran
    names = [e["name"] for e in events if e["type"] == "tool_call"]
    assert "recall_insights" in names
    assert names.count("get_period_summary") == 2
    assert "compare_periods" in names
    assert "drill_down" in names
    assert "get_positions" in names

    # report content
    assert report["headline"] and "NVDA" in report["headline"]
    assert len(report["advisements"]) >= 3
    assert all(a["title"] and a["detail"] for a in report["advisements"])
    assert report["what_changed"] and report["why"] and report["drivers"]
    assert "prior_insight_review" in report
    assert report["confidence"] == 0.6
    assert report["market_context"] == []               # no Tavily key

    # a concentration-driven paper trade was proposed against a real long position
    assert len(report["proposed_trades"]) == 1
    pt = report["proposed_trades"][0]
    assert pt["symbol"] == "NVDA" and pt["side"] == "SELL" and 1 <= pt["qty"] <= 3
    assert pt["order_type"] == "MKT"

    # insights persisted (headline + advisements)
    saved = memory.recall_insights(50)
    assert len(saved) == 1 + len(report["advisements"])
    assert any(s["kind"] == "observation" for s in saved)
    assert any(s["kind"] == "advisement" for s in saved)

    # run row done + report/events persisted
    with get_conn() as conn:
        row = dict(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
    assert row["status"] == "done"
    assert row["prism_session_id"] == run_id
    assert json.loads(row["report_json"])["headline"] == report["headline"]
    assert len(json.loads(row["events_json"])) == len(events)


def test_prior_insights_are_reviewed(fake_analytics):
    from app.agent import memory
    from app.agent.runner import run_analysis
    from app.db import get_conn, now_iso

    prior = memory.save_insight("r_old", "advisement", "Reduce NVDA concentration below 30%", ["lot_1"])
    prior2 = memory.save_insight("r_old", "advisement", "Cap monthly fees", ["fact#5"])

    run_id = "r_test2"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, created_at, period_a, period_b, question, status) VALUES (?,?,?,?,?,?)",
            (run_id, now_iso(), "2026-06", "2026-07", "", "running"),
        )
    report = asyncio.run(run_analysis(run_id, "2026-06", "2026-07", None))

    review = {r["insight_id"]: r for r in report["prior_insight_review"]}
    assert prior["id"] in review and prior2["id"] in review
    # concentration rose 0.28 -> 0.61 so the prior advice was not followed
    assert review[prior["id"]]["verdict"] == "invalidated"
    assert "0.28" in review[prior["id"]]["note"] or "28%" in review[prior["id"]]["note"]
    # fees rose 90 -> 165
    assert review[prior2["id"]]["verdict"] == "invalidated"


def test_run_error_path_publishes_error(monkeypatch):
    """A broken analytics module ends as an `error` event and an error run row."""
    from app.agent.events import bus
    from app.agent.runner import run_analysis
    from app.db import get_conn, now_iso

    def boom(a, b):
        raise RuntimeError("analytics exploded")

    fake = make_fake_service()
    fake.get_compare = boom
    monkeypatch.setitem(sys.modules, "app.analytics.service", fake)

    run_id = "r_err"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, created_at, period_a, period_b, question, status) VALUES (?,?,?,?,?,?)",
            (run_id, now_iso(), "2026-06", "2026-07", "", "running"),
        )
    asyncio.run(run_analysis(run_id, "2026-06", "2026-07", None))

    kinds = [e["type"] for e in bus.events(run_id)]
    assert kinds[-1] == "error"
    with get_conn() as conn:
        row = dict(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
    # the tool wrapper swallows the tool exception (so one bad tool cannot kill the run)
    # and the runner then aborts on the missing comparison
    assert row["status"] == "error" and "compare_periods" in (row["error"] or "")
    assert bus.events(run_id)[-1]["message"]


# --------------------------------------------------------------------------- fallback
def test_fallback_market_context_from_web():
    from app.agent.fallback import build_fallback_report

    macro = [
        {"title": "Nasdaq slides 4% in July", "url": "https://ex.com/a", "content": "AI names sold off hard " * 12},
        {"title": "Fed holds rates", "url": "https://ex.com/b", "content": "The FOMC left rates unchanged."},
    ]
    rep = build_fallback_report(COMPARE, [], [], macro)
    assert len(rep["market_context"]) == 2
    assert all(b.endswith("]") and "[source: https://ex.com/" in b for b in rep["market_context"])
    assert "https://ex.com/a" in rep["sources"] and "https://ex.com/b" in rep["sources"]


def test_fallback_no_positions_means_no_trade():
    from app.agent.fallback import build_fallback_report

    rep = build_fallback_report(dict(COMPARE), [], [], [])
    assert rep["proposed_trades"] == []          # positions were not supplied
    rep2 = build_fallback_report({**COMPARE, "_positions": POSITIONS}, [], [], [])
    assert rep2["proposed_trades"][0]["symbol"] == "NVDA"


# --------------------------------------------------------------------------- llm json
@pytest.mark.parametrize(
    "raw",
    [
        '{"headline": "ok"}',
        '```json\n{"headline": "ok"}\n```',
        'Sure! Here is the report:\n```\n{"headline": "ok"}\n```\nHope that helps.',
        'Thinking... {"headline": "ok"} <-- done',
        '{"headline": "ok",}',                      # trailing comma
        '\n\n  {"headline": "ok"}  \n\n',
    ],
)
def test_extract_json_robustness(raw):
    from app.integrations.llm import extract_json

    assert extract_json(raw) == {"headline": "ok"}


def test_extract_json_gives_up_cleanly():
    from app.integrations.llm import extract_json

    assert extract_json("no json at all") is None
    assert extract_json("") is None
    assert extract_json(None) is None


def test_chat_json_unavailable_without_config():
    from app.integrations.llm import llm

    parsed, meta = llm.chat_json("sys", "user")
    assert parsed is None and meta["error"] == "llm_unavailable"


# --------------------------------------------------------------------------- coercion
def test_report_coercion_of_sloppy_llm_output():
    from app.agent.runner import _coerce_report

    fb = {"headline": "fallback", "advisements": [], "prior_insight_review": []}
    sloppy = {
        "report": {
            "summary": "P&L fell 63% driven by NVDA earnings calls",
            "changes": "Realized P&L fell $2,140",                 # string, not list
            "why": ["NVDA calls", {"text": "over-trading"}],       # mixed types
            "drivers": [{"key": "NVDA", "contribution": "0.71", "note": "3 lots",
                         "evidence_lot_ids": ["lot_1"]}],
            "recommendations": ["Cap single-underlying exposure at 30% of monthly buys"],
            "trades": [{"symbol": "NVDA", "side": "sell", "quantity": 2, "order_type": "mkt", "tif": "day"}],
            "insight_review": [{"id": "i_1", "text": "cut concentration", "verdict": "GOOD"}],
            "confidence": "1.9",
            "macro": ["Nasdaq fell [source: https://x.com]"],
            "unknown_field": 123,
        }
    }
    rep, ok = _coerce_report(sloppy, fb)
    assert ok is True
    assert rep["headline"].startswith("P&L fell 63%")
    assert rep["what_changed"] == ["Realized P&L fell $2,140"]
    assert rep["why"] == ["NVDA calls", "over-trading"]
    assert rep["drivers"][0]["name"] == "NVDA" and rep["drivers"][0]["contribution_pct"] == 0.71
    assert rep["advisements"][0]["priority"] == "medium"
    assert rep["proposed_trades"][0] == {
        "id": "pt_1", "symbol": "NVDA", "side": "SELL", "qty": 2.0,
        "order_type": "MKT", "limit_price": None, "tif": "DAY", "rationale": "",
    }
    assert rep["prior_insight_review"][0]["verdict"] == "unclear"   # "GOOD" is not a legal verdict
    assert rep["prior_insight_review"][0]["insight_id"] == "i_1"
    assert rep["confidence"] == 1.0                                  # clamped
    assert rep["market_context"] == ["Nasdaq fell [source: https://x.com]"]
    assert "unknown_field" not in rep


def test_report_coercion_falls_back_when_hopeless():
    from app.agent.runner import _coerce_report

    fb = {"headline": "fallback", "advisements": [], "prior_insight_review": [], "confidence": 0.6}
    rep, ok = _coerce_report({"nothing": "useful"}, fb)
    assert ok is True and rep["headline"] == "fallback"   # headline defaulted from fallback


# --------------------------------------------------------------------------- routes / SSE
def test_analyze_and_sse_stream(fake_analytics):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routes.agent import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    # context manager => one long-lived portal, so the background asyncio task survives
    with TestClient(app) as client:
        _sse_flow(client)


def _sse_flow(client):
    r = client.post("/api/analyze", json={"question": "why?"})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert r.json()["a"] == "2026-06" and r.json()["b"] == "2026-07"

    events: list[dict] = []
    with client.stream("GET", f"/api/analyze/{run_id}/events") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            ev = json.loads(line[5:].strip())
            events.append(ev)
            if ev["type"] in ("final", "error"):
                break

    assert events[-1]["type"] == "final", [e["type"] for e in events]
    assert events[-1]["report"]["headline"]
    assert events[-1]["prism"]["session_id"] == run_id

    runs = client.get("/api/runs").json()["runs"]
    assert runs[0]["id"] == run_id and runs[0]["status"] == "done"
    assert runs[0]["headline"]

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["report"]["headline"] == events[-1]["report"]["headline"]
    assert len(detail["events"]) >= len(events)

    ins = client.get("/api/insights").json()["insights"]
    assert ins, "insights should have been saved by the run"
    patched = client.patch(f"/api/insights/{ins[0]['id']}", json={"status": "followed"})
    assert patched.status_code == 200 and patched.json()["status"] == "followed"
    assert client.patch(f"/api/insights/{ins[0]['id']}", json={"status": "bogus"}).status_code == 422
    assert client.get("/api/runs/nope").status_code == 404


def test_prism_status_route_when_unconfigured():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routes.agent import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    body = TestClient(app).get("/api/prism/status").json()
    assert body["configured"] is False
    assert body["dashboard_url"] == "https://prism.blockconvey.com"
    assert body["live_connected"] is False and body["credential_ok"] is False


def test_sse_replays_for_late_subscriber(fake_analytics):
    """A subscriber that joins after the run finished still receives the whole stream."""
    from app.agent.events import bus
    from app.agent.runner import run_analysis
    from app.db import get_conn, now_iso

    run_id = "r_late"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, created_at, period_a, period_b, question, status) VALUES (?,?,?,?,?,?)",
            (run_id, now_iso(), "2026-06", "2026-07", "", "running"),
        )
    asyncio.run(run_analysis(run_id, "2026-06", "2026-07", None))

    async def collect():
        return [ev async for ev in bus.subscribe(run_id)]

    replay = asyncio.run(collect())
    assert replay[-1]["type"] == "final"
    assert len(replay) == len(bus.events(run_id))
