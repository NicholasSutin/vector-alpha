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

    # headline names the underlying driver (not the asset class) and its strategy tag
    assert "NVDA earnings-week calls" in report["headline"], report["headline"]
    assert "6 lots" in report["headline"]      # NVDA lots closed in period b, not a fixed number

    # a concentration-driven paper trade trims 25% of the NVDA long (9 shares -> 3)
    assert len(report["proposed_trades"]) == 1
    pt = report["proposed_trades"][0]
    assert pt["symbol"] == "NVDA" and pt["side"] == "SELL" and pt["qty"] == 3
    assert pt["order_type"] == "MKT" and pt["tif"] == "DAY"

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


def test_fallback_trade_rebalances_into_spy_without_a_trimmable_long():
    from app.agent.fallback import build_fallback_report

    # concentration is high (0.61) but nothing is held -> buy 1 SPY to rebalance
    rep = build_fallback_report(dict(COMPARE), [], [], [])
    assert rep["proposed_trades"][0]["symbol"] == "SPY"
    assert rep["proposed_trades"][0]["side"] == "BUY" and rep["proposed_trades"][0]["qty"] == 1

    # an NVDA long exists -> trim 25% of it instead
    rep2 = build_fallback_report({**COMPARE, "_positions": POSITIONS}, [], [], [])
    assert rep2["proposed_trades"][0] == {
        **rep2["proposed_trades"][0], "symbol": "NVDA", "side": "SELL", "qty": 3.0}

    # low concentration -> no trade at all
    low = {**COMPARE, "b": {**COMPARE["b"], "concentration_top_share": 0.2},
           "_positions": POSITIONS}
    assert build_fallback_report(low, [], [], [])["proposed_trades"] == []


def test_fallback_headline_prefers_the_underlying_driver():
    from app.agent.fallback import build_fallback_report

    lots = {"NVDA": [{"lot_id": f"lot_{i}", "strategy_tag": "earnings", "asset_type": "option"}
                     for i in range(1, 4)]}
    rep = build_fallback_report({**COMPARE, "_lots_by_driver": lots}, [], [], [])
    assert "NVDA earnings-week calls" in rep["headline"]
    assert "3 lots" in rep["headline"]
    assert "option" not in rep["headline"].split("driven by")[1].split("(")[0]
    # the asset_type driver still shows up in `why`
    assert any("option" in w for w in rep["why"])


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


# --------------------------------------------------------------------------- narrative layer
def _narrative_llm(monkeypatch, payload, *, latency_ms=4000, error=None):
    """Point the LLM client at a canned narrative response."""
    from app.config import settings
    from app.integrations import llm as llm_mod

    monkeypatch.setattr(llm_mod.llm, "available", lambda: True)
    object.__setattr__(settings, "llm_model", "local")
    calls: list[tuple[str, str, int, float]] = []

    def fake_chat_json(system, user, max_tokens=1500, temperature=0.2):
        calls.append((system, user, max_tokens, temperature))
        meta = {"model": "local", "latency_ms": latency_ms, "tokens_in": 700,
                "tokens_out": 90, "raw_text": json.dumps(payload or {}), "error": error}
        return payload, meta

    monkeypatch.setattr(llm_mod.llm, "chat_json", fake_chat_json)
    return calls


def test_narrative_layer_merges_over_deterministic_report(fake_analytics, monkeypatch):
    from app.agent.runner import run_analysis
    from app.db import get_conn, now_iso

    narrative = {
        "headline": "P&L fell 63% on NVDA earnings-week calls, amplified by over-trading",
        "why": ["Three NVDA call lots expired near-worthless (lot_1, lot_2, lot_3)",
                "Hold time collapsed so winners were cut early"],
        "behaviour": ["Trade count +86% while win rate fell to 41%"],
        "market_context": ["Nasdaq sold off in July [source: https://ex.com/a]"],
        "company_changes": [{"symbol": "NVDA", "text": "Guided Q3 below consensus."}],
    }
    calls = _narrative_llm(monkeypatch, narrative)

    run_id = "r_narr"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, created_at, period_a, period_b, question, status) VALUES (?,?,?,?,?,?)",
            (run_id, now_iso(), "2026-06", "2026-07", "", "running"),
        )
    report = asyncio.run(run_analysis(run_id, "2026-06", "2026-07", None))

    # the prose came from the model ...
    assert report["headline"] == narrative["headline"]
    assert report["why"] == narrative["why"]
    assert report["behaviour"] == narrative["behaviour"]
    assert report["market_context"] == narrative["market_context"]
    assert report["confidence"] == 0.75
    # ... while every computed field stayed with the engine
    assert len(report["advisements"]) >= 3
    assert report["drivers"][0]["name"] == "NVDA"
    assert report["what_changed"][0].startswith("Realized P&L fell")
    # the engine's paper trade survives the merge untouched
    assert report["proposed_trades"][0]["symbol"] == "NVDA"
    assert report["proposed_trades"][0]["side"] == "SELL" and report["proposed_trades"][0]["qty"] == 3.0

    # ONE call, small prompt, small completion, no retry budget
    assert len(calls) == 1
    system, user, max_tokens, temperature = calls[0]
    assert max_tokens == 320 and temperature == 0.2
    assert len(user) <= 3000, len(user)
    assert "under 120 words" in system.lower()

    with get_conn() as conn:
        row = dict(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
    assert row["status"] == "done" and row["model"] == "local"


def test_narrative_failure_keeps_the_deterministic_report(fake_analytics, monkeypatch):
    from app.agent.events import bus
    from app.agent.runner import run_analysis
    from app.db import get_conn, now_iso

    _narrative_llm(monkeypatch, None, latency_ms=150_000, error="llm_unavailable: Request timed out.")

    run_id = "r_narr_fail"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, created_at, period_a, period_b, question, status) VALUES (?,?,?,?,?,?)",
            (run_id, now_iso(), "2026-06", "2026-07", "", "running"),
        )
    report = asyncio.run(run_analysis(run_id, "2026-06", "2026-07", None))

    assert "NVDA" in report["headline"] and report["confidence"] == 0.6
    final = [e for e in bus.events(run_id) if e["type"] == "final"][0]
    assert final["fallback"] is True
    msgs = [e.get("message", "") for e in bus.events(run_id) if e["type"] == "status"]
    assert any("Narrative layer fallback (150 s)" in m for m in msgs), msgs


def test_narrative_prompt_stays_within_budget():
    from app.agent.prompts import build_narrative_message

    big = {**COMPARE, "facts": [f"A very long generated fact sentence number {i}. " * 6 for i in range(40)]}
    msg = build_narrative_message(
        period_a="2026-06", period_b="2026-07", compare=big,
        prior_insights=[{"text": "x" * 500}] * 10, context_insights=[{"text": "y" * 500}] * 10,
        macro=[{"title": "t" * 200, "url": "https://e.com", "content": "c" * 900}] * 6,
        company=[{"symbol": "NVDA", "text": "z" * 900}] * 5,
        question="q" * 400,
    )
    assert len(msg) <= 3000
    assert msg.endswith("Return ONLY the JSON object. Under 120 words.")


def test_merge_narrative_rejects_junk():
    from app.agent.runner import _merge_narrative

    base = {"headline": "engine", "why": ["engine why"], "behaviour": [], "what_changed": [],
            "drivers": [], "advisements": [], "proposed_trades": [], "prior_insight_review": [],
            "confidence": 0.6, "sources": [], "market_context": [], "company_changes": []}
    assert _merge_narrative(dict(base), None) is False
    assert _merge_narrative(dict(base), {}) is False
    assert _merge_narrative(dict(base), {"headline": "   "}) is False   # nothing usable
    rep = dict(base)
    assert _merge_narrative(rep, {"headline": "model wrote this"}) is True
    assert rep["headline"] == "model wrote this" and rep["confidence"] == 0.75


# --------------------------------------------------------------------------- company context
def _enable_tavily(monkeypatch, results_by_query):
    from app.config import settings
    from app.integrations import tavily as tavily_mod

    object.__setattr__(settings, "tavily_api_key", "tv-test")
    seen: list[str] = []

    def fake_search(query, max_results=5):
        seen.append(query)
        return results_by_query.get(query.split()[0], [])[:max_results]

    monkeypatch.setattr(tavily_mod, "web_search", fake_search)
    return seen


def test_company_context_populates_report_and_memory(fake_analytics, monkeypatch):
    from app.agent import memory
    from app.agent.events import bus
    from app.agent.runner import run_analysis
    from app.db import get_conn, now_iso

    results = {
        "NVDA": [{"title": "NVDA guides Q3 below consensus", "url": "https://n.com/1",
                  "content": "Nvidia said data-centre revenue would grow slower. " * 6},
                 {"title": "NVDA falls 8%", "url": "https://n.com/2", "content": "shares fell"}],
        "TSLA": [{"title": "TSLA deliveries miss", "url": "https://t.com/1", "content": "deliveries missed"}],
        "option": [], "stock": [], "Federal": [], "earnings": [],
    }
    seen = _enable_tavily(monkeypatch, results)
    _narrative_llm(monkeypatch, {"headline": "narrated", "company_changes": [
        {"symbol": "NVDA", "text": "Cut its Q3 outlook, and the calls never recovered."}]})

    run_id = "r_company"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, created_at, period_a, period_b, question, status) VALUES (?,?,?,?,?,?)",
            (run_id, now_iso(), "2026-06", "2026-07", "", "running"),
        )
    report = asyncio.run(run_analysis(run_id, "2026-06", "2026-07", None))

    assert any("NVDA stock July 2026 earnings guidance news" == q for q in seen), seen
    by_sym = {c["symbol"]: c for c in report["company_changes"]}
    assert set(by_sym) == {"NVDA", "TSLA"}
    # the model rewrote NVDA's prose; sources stay engine-supplied
    assert by_sym["NVDA"]["text"] == "Cut its Q3 outlook, and the calls never recovered."
    assert by_sym["NVDA"]["sources"] == ["https://n.com/1", "https://n.com/2"]
    # TSLA keeps the deterministic title + snippet
    assert by_sym["TSLA"]["text"].startswith("TSLA deliveries miss —")
    assert "https://n.com/1" in report["sources"]

    # traced + streamed
    names = [e["name"] for e in bus.events(run_id) if e["type"] == "tool_call"]
    assert "company_context" in names

    # persisted as `context` insights for the next run
    ctx = [i for i in memory.recall_insights(100) if i["kind"] == "context"]
    assert {i["text"].split()[0] for i in ctx} == {"NVDA", "TSLA"}
    assert all(i["text"].split()[1].startswith("2026-07:") for i in ctx)
    assert ctx[0]["evidence"], "sources should be stored as evidence"

    # a second run must not duplicate identical context
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, created_at, period_a, period_b, question, status) VALUES (?,?,?,?,?,?)",
            ("r_company2", now_iso(), "2026-06", "2026-07", "", "running"),
        )
    asyncio.run(run_analysis("r_company2", "2026-06", "2026-07", None))
    ctx2 = [i for i in memory.recall_insights(200) if i["kind"] == "context"]
    assert len(ctx2) == len(ctx)


def test_no_tavily_key_skips_company_context(fake_analytics):
    from app.agent.events import bus
    from app.agent.runner import run_analysis
    from app.db import get_conn, now_iso

    run_id = "r_notavily"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, created_at, period_a, period_b, question, status) VALUES (?,?,?,?,?,?)",
            (run_id, now_iso(), "2026-06", "2026-07", "", "running"),
        )
    report = asyncio.run(run_analysis(run_id, "2026-06", "2026-07", None))

    assert report["company_changes"] == [] and report["market_context"] == []
    msgs = [e.get("message", "") for e in bus.events(run_id) if e["type"] == "status"]
    assert "Tavily key not set — skipping company context" in msgs
    assert "Tavily key not set — skipping market context" in msgs
    names = [e["name"] for e in bus.events(run_id) if e["type"] == "tool_call"]
    assert "company_context" not in names


def test_known_business_context_reaches_the_prompt(fake_analytics, monkeypatch):
    from app.agent import memory
    from app.agent.runner import run_analysis
    from app.db import get_conn, now_iso

    memory.save_insight("r_prev", "context", "NVDA 2026-06: guided Q2 above consensus", ["https://n.com/0"])
    calls = _narrative_llm(monkeypatch, {"headline": "h"})

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, created_at, period_a, period_b, question, status) VALUES (?,?,?,?,?,?)",
            ("r_ctx", now_iso(), "2026-06", "2026-07", "", "running"),
        )
    asyncio.run(run_analysis("r_ctx", "2026-06", "2026-07", None))

    user = calls[0][1]
    assert "KNOWN BUSINESS CONTEXT" in user
    assert "guided Q2 above consensus" in user


# --------------------------------------------------------------------------- retry policy
def test_no_json_retry_after_a_slow_first_call(monkeypatch):
    """A nudge costs a whole second generation — never spend it when the first call was slow."""
    import time as _time

    from app.config import settings
    from app.integrations import llm as llm_mod

    object.__setattr__(settings, "llm_base_url", "http://x/v1")
    object.__setattr__(settings, "llm_model", "local")
    object.__setattr__(settings, "llm_timeout_s", 150.0)
    calls = {"n": 0}

    class _Msg:
        content = "not json at all"

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]
        usage = None
        model = "local"

    def slow_call(self, messages, max_tokens, temperature, json_mode):
        calls["n"] += 1
        _time.sleep(0.01)
        return _Resp()

    monkeypatch.setattr(llm_mod.LLMClient, "_call", slow_call)
    monkeypatch.setattr(llm_mod.llm, "_client", object())          # skip real client construction

    # fast first call (0.01s of a 150s budget) -> the nudge is worth it
    parsed, meta = llm_mod.llm.chat_json("s", "u")
    assert parsed is None and calls["n"] == 2

    # the same call against a 0.001s budget is "slow" -> no nudge
    calls["n"] = 0
    object.__setattr__(settings, "llm_timeout_s", 0.001)
    parsed, meta = llm_mod.llm.chat_json("s", "u")
    assert parsed is None and calls["n"] == 1
    object.__setattr__(settings, "llm_timeout_s", 150.0)
