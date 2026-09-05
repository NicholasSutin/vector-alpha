"""The "Explain the Change" agent loop.

Deterministic tool sequence (small models cannot be trusted to plan a tool loop):
    recall_insights -> get_period_summary(a) -> get_period_summary(b) -> compare_periods
    -> drill_down x3 -> get_positions -> [web_search] -> [macro_context] -> llm_reasoning
Every step is published to the SSE bus AND recorded as a PRISM trajectory step.
Session id == run id, so PRISM assembles one trajectory per run.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Callable

from app.agent import fallback as fallback_mod
from app.agent import memory
from app.agent.events import bus
from app.agent.prompts import NARRATIVE_SYSTEM_PROMPT, build_narrative_message
from app.config import settings
from app.db import get_conn, now_iso
from app.integrations.llm import llm
from app.integrations.prism import prism
from app.models import Report

log = logging.getLogger("vector-alpha.runner")

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def _month_name(period: str) -> str:
    try:
        y, m = period.split("-")[:2]
        return f"{MONTHS[int(m) - 1]} {y}"
    except Exception:
        return period


def _summarize(value: Any, limit: int = 300) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, default=str)[:limit]
    except Exception:
        return str(value)[:limit]


class _Trajectory:
    """Collects PRISM trajectory steps (schema per prismtrace.PRISMtrace.submit_trajectory)."""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def add(
        self,
        *,
        step_type: str,
        label: str,
        tool_name: str | None = None,
        input_summary: Any = None,
        output_summary: Any = None,
        duration_ms: int = 0,
        status: str = "success",
        token_count: int = 0,
    ) -> None:
        step: dict[str, Any] = {
            "step_type": step_type,
            "label": label,
            "input_summary": _summarize(input_summary, 600),
            "output_summary": _summarize(output_summary, 600),
            "duration_ms": int(duration_ms),
            "status": status,
            "token_count": int(token_count),
        }
        if tool_name:
            step["tool_name"] = tool_name
        self.steps.append(step)


async def _tool(
    run_id: str,
    traj: _Trajectory,
    name: str,
    fn: Callable[..., Any],
    tool_input: dict[str, Any] | None = None,
    label: str | None = None,
) -> Any:
    """Run one blocking tool off-thread, publish call/result events, record the PRISM step."""
    tool_input = tool_input or {}
    bus.publish(run_id, {"type": "tool_call", "name": name, "input": tool_input})
    t0 = time.perf_counter()
    status, result, err = "success", None, None
    try:
        result = await asyncio.to_thread(fn)
    except Exception as e:
        status, err = "error", str(e)[:300]
        log.warning("tool %s failed: %s", name, e)
    ms = int((time.perf_counter() - t0) * 1000)

    if status == "success":
        if isinstance(result, list):
            summary = f"{len(result)} items"
            if result and isinstance(result[0], dict):
                summary += ": " + _summarize([r.get("lot_id") or r.get("id") or r.get("symbol") or r.get("key")
                                              for r in result[:6]], 200)
        elif isinstance(result, dict):
            summary = _summarize({k: v for k, v in result.items() if not isinstance(v, (list, dict))}, 280) or "ok"
        else:
            summary = _summarize(result, 280)
    else:
        summary = f"error: {err}"

    bus.publish(run_id, {"type": "tool_result", "name": name, "summary": summary[:300]})
    traj.add(step_type="tool_call", label=label or name, tool_name=name,
             input_summary=tool_input, output_summary=summary, duration_ms=ms, status=status)
    return result


def _status(run_id: str, message: str) -> None:
    bus.publish(run_id, {"type": "status", "message": message})


def _persist_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))


def _slist(v: Any) -> list[str]:
    """Coerce whatever a small model produced into a list of strings."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    out = []
    for x in v if isinstance(v, list) else [v]:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            out.append(str(x.get("text") or x.get("detail") or x.get("title") or json.dumps(x, default=str)))
        else:
            out.append(str(x))
    return out


def _merge_narrative(report: dict[str, Any], narrative: dict[str, Any] | None) -> bool:
    """Overlay the model's prose on the engine-computed report, IN PLACE.

    Only the narrative fields move; what_changed, drivers, advisements, proposed_trades and
    prior_insight_review stay exactly as the deterministic engine computed them.
    Returns True when the merge produced a valid Report.
    """
    if not isinstance(narrative, dict):
        return False
    candidate = dict(report)

    headline = narrative.get("headline")
    if isinstance(headline, str) and headline.strip():
        candidate["headline"] = headline.strip()

    for field in ("why", "behaviour", "market_context"):
        vals = [v for v in _slist(narrative.get(field)) if v.strip()]
        if vals:
            candidate[field] = vals

    # the model may rewrite the prose of a company change, never its symbol or sources
    rewritten: dict[str, str] = {}
    for c in narrative.get("company_changes") or []:
        if isinstance(c, dict) and c.get("symbol") and isinstance(c.get("text"), str):
            rewritten[str(c["symbol"]).upper()] = c["text"].strip()
    if rewritten:
        merged_cc = []
        for c in candidate.get("company_changes") or []:
            c = dict(c)
            new_text = rewritten.get(str(c.get("symbol", "")).upper())
            if new_text:
                c["text"] = new_text
            merged_cc.append(c)
        candidate["company_changes"] = merged_cc

    if candidate.get("headline") == report.get("headline") and candidate.get("why") == report.get("why"):
        return False                      # the model added nothing usable

    candidate["confidence"] = 0.75
    validated, ok = _coerce_report(candidate, report)
    if not ok:
        return False
    report.clear()
    report.update(validated)
    return True


def _save_company_context(run_id: str, period_b: str, changes: list[dict[str, Any]]) -> int:
    """Persist company context as `context` insights so later runs inherit the business story.
    Deduped on symbol+period+text."""
    saved = 0
    try:
        existing = {i.get("text") for i in memory.recall_insights(200) if i.get("kind") == "context"}
    except Exception:
        existing = set()
    for c in changes:
        sym = str(c.get("symbol", "")).strip()
        text = str(c.get("text", "")).strip()
        if not sym or not text:
            continue
        line = f"{sym} {period_b}: {text}"[:800]
        if line in existing:
            continue
        try:
            memory.save_insight(run_id, "context", line, list(c.get("sources") or []))
            existing.add(line)
            saved += 1
        except Exception as e:
            log.warning("saving company context failed: %s", e)
    return saved


def _coerce_report(raw: dict[str, Any], fb: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Validate an LLM dict against models.Report; coerce common small-model slips.
    Returns (report_dict, ok)."""
    try:
        return Report.model_validate(raw).model_dump(), True
    except Exception:
        pass

    d = dict(raw)
    # unwrap {"report": {...}}
    if "report" in d and isinstance(d["report"], dict):
        d = dict(d["report"])

    aliases = {
        "summary": "headline", "title": "headline", "one_liner": "headline",
        "changes": "what_changed", "what_changed_bullets": "what_changed",
        "reasons": "why", "causes": "why",
        "recommendations": "advisements", "advice": "advisements", "actions": "advisements",
        "trades": "proposed_trades", "proposedTrades": "proposed_trades",
        "behaviour_flags": "behaviour", "behavior": "behaviour",
        "urls": "sources", "citations": "sources",
        "prior_insights": "prior_insight_review", "insight_review": "prior_insight_review",
        "market": "market_context", "macro": "market_context", "macro_context": "market_context",
    }
    for src, dst in aliases.items():
        if src in d and dst not in d:
            d[dst] = d.pop(src)

    d["headline"] = str(d.get("headline") or fb.get("headline") or "Period comparison")
    for k in ("what_changed", "why", "behaviour", "sources", "market_context"):
        d[k] = _slist(d.get(k))

    cc = []
    for x in (d.get("company_changes") or []):
        if isinstance(x, dict) and x.get("symbol"):
            cc.append({"symbol": str(x["symbol"]), "text": str(x.get("text") or ""),
                       "sources": _slist(x.get("sources"))})
    d["company_changes"] = cc

    # drivers
    dv = []
    for x in (d.get("drivers") or []):
        if isinstance(x, str):
            dv.append({"name": x, "contribution_pct": 0, "detail": "", "evidence": []})
        elif isinstance(x, dict):
            dv.append({
                "name": str(x.get("name") or x.get("key") or ""),
                "contribution_pct": float(x.get("contribution_pct") or x.get("contribution") or 0) or 0.0,
                "detail": str(x.get("detail") or x.get("note") or ""),
                "evidence": _slist(x.get("evidence") or x.get("evidence_lot_ids")),
            })
    d["drivers"] = dv

    # advisements
    adv = []
    for x in (d.get("advisements") or []):
        if isinstance(x, str):
            adv.append({"title": x[:120], "detail": x, "priority": "medium", "evidence": []})
        elif isinstance(x, dict):
            pr = str(x.get("priority", "medium")).lower()
            adv.append({
                "title": str(x.get("title") or x.get("name") or x.get("detail", ""))[:200] or "Advisement",
                "detail": str(x.get("detail") or x.get("description") or x.get("title") or ""),
                "priority": pr if pr in ("high", "medium", "low") else "medium",
                "evidence": _slist(x.get("evidence")),
            })
    d["advisements"] = adv or fb.get("advisements", [])

    # proposed trades
    pts = []
    for i, x in enumerate(d.get("proposed_trades") or [], 1):
        if not isinstance(x, dict):
            continue
        side = str(x.get("side", "")).upper()
        if side not in ("BUY", "SELL"):
            continue
        try:
            qty = float(x.get("qty") or x.get("quantity") or 1)
        except Exception:
            qty = 1
        ot = str(x.get("order_type", "MKT")).upper()
        tif = str(x.get("tif", "DAY")).upper()
        pts.append({
            "id": str(x.get("id") or f"pt_{i}"),
            "symbol": str(x.get("symbol") or x.get("ticker") or ""),
            "side": side,
            "qty": max(1.0, min(qty, 100.0)),
            "order_type": ot if ot in ("MKT", "LMT") else "MKT",
            "limit_price": x.get("limit_price"),
            "tif": tif if tif in ("DAY", "GTC") else "DAY",
            "rationale": str(x.get("rationale") or ""),
        })
    d["proposed_trades"] = [p for p in pts if p["symbol"]]

    # prior insight review
    pir = []
    for x in (d.get("prior_insight_review") or []):
        if not isinstance(x, dict):
            continue
        v = str(x.get("verdict", "unclear")).lower()
        pir.append({
            "insight_id": str(x.get("insight_id") or x.get("id") or ""),
            "text": str(x.get("text") or ""),
            "verdict": v if v in ("followed", "ignored", "validated", "invalidated", "unclear") else "unclear",
            "note": str(x.get("note") or ""),
        })
    d["prior_insight_review"] = pir or fb.get("prior_insight_review", [])

    try:
        c = float(d.get("confidence", 0.5))
    except Exception:
        c = 0.5
    d["confidence"] = min(1.0, max(0.0, c))
    d = {k: v for k, v in d.items() if k in Report.model_fields}

    try:
        return Report.model_validate(d).model_dump(), True
    except Exception as e:
        log.warning("report coercion failed: %s", e)
        return fb, False


async def run_analysis(run_id: str, a: str, b: str, question: str | None = None) -> dict[str, Any]:
    """Run the whole analysis. Never raises: errors become an `error` event + run row."""
    t_start = time.perf_counter()
    traj = _Trajectory()
    model_used = "fallback-deterministic"
    used_fallback = True
    report: dict[str, Any] = {}

    try:
        from app.analytics import service as analytics  # lazy: built by another module

        _status(run_id, f"Analyzing {a} → {b}")

        prior = await _tool(run_id, traj, "recall_insights",
                            lambda: memory.recall_insights(20), {"limit": 20},
                            label="recall prior insights") or []

        _status(run_id, "Loading period summaries")
        await _tool(run_id, traj, "get_period_summary",
                    lambda: analytics.get_summary(a), {"period": a})
        await _tool(run_id, traj, "get_period_summary",
                    lambda: analytics.get_summary(b), {"period": b})

        _status(run_id, "Comparing periods")
        compare = await _tool(run_id, traj, "compare_periods",
                              lambda: analytics.get_compare(a, b), {"a": a, "b": b})
        if not compare:
            raise RuntimeError("compare_periods returned nothing")
        if hasattr(compare, "model_dump"):
            compare = compare.model_dump()

        drivers = list(compare.get("drivers") or [])
        top_keys: list[str] = []
        for d in drivers:
            k = str(d.get("key", ""))
            if d.get("dimension") == "underlying" and k and k not in top_keys:
                top_keys.append(k)
            if len(top_keys) >= 3:
                break
        if not top_keys:
            top_keys = [str(d.get("key", "")) for d in drivers[:3] if d.get("key")]

        lots_by_driver: dict[str, list[dict]] = {}
        if top_keys:
            _status(run_id, f"Drilling into top drivers: {', '.join(top_keys)}")
        for key in top_keys:
            lots = await _tool(
                run_id, traj, "drill_down",
                (lambda k=key: analytics.get_lots(period=b, underlying=k, limit=50)),
                {"period": b, "underlying": key},
                label=f"drill_down {key}",
            ) or []
            lots_by_driver[key] = [l.model_dump() if hasattr(l, "model_dump") else l for l in lots][:10]

        positions = await _tool(run_id, traj, "get_positions",
                                lambda: analytics.get_positions(), {}) or []
        positions = [p.model_dump() if hasattr(p, "model_dump") else p for p in positions]

        # ---- web research (driver specific) ----
        web: list[dict] = []
        macro: list[dict] = []
        if settings.has_tavily:
            from app.integrations.tavily import web_search

            if top_keys:
                q = f"{top_keys[0]} stock {_month_name(b)} earnings move"
                _status(run_id, f"Searching the web: {q}")
                web = await _tool(run_id, traj, "web_search",
                                  (lambda qq=q: web_search(qq, max_results=3)),
                                  {"query": q, "max_results": 3}) or []

            # ---- macro / market regime context ----
            mb = _month_name(b)
            queries = [
                f"stock market recap {mb} S&P 500 Nasdaq VIX",
                f"Federal Reserve rates inflation {mb}",
            ]
            if top_keys:
                queries.append(f"{top_keys[0]} stock {mb} news")
            _status(run_id, f"Gathering market context for {mb}")
            t0 = time.perf_counter()
            seen_urls = {w.get("url") for w in web}
            for q in queries:
                res = await asyncio.to_thread(web_search, q, 4) or []
                for r in res:
                    if r.get("url") and r["url"] not in seen_urls:
                        seen_urls.add(r["url"])
                        macro.append(r)
            ms = int((time.perf_counter() - t0) * 1000)
            bus.publish(run_id, {"type": "tool_call", "name": "macro_context",
                                 "input": {"queries": queries, "max_results": 4}})
            bus.publish(run_id, {"type": "tool_result", "name": "macro_context",
                                 "summary": f"{len(macro)} market snippets from {len(queries)} searches"[:300]})
            traj.add(step_type="tool_call", label="macro_context", tool_name="macro_context",
                     input_summary={"queries": queries}, duration_ms=ms,
                     output_summary=f"{len(macro)} snippets: " +
                                    _summarize([m.get("title") for m in macro[:5]], 300))
        else:
            _status(run_id, "Tavily key not set — skipping market context")

        # ---- company context: what changed at the top driver companies ----
        company: list[dict[str, Any]] = []
        if settings.has_tavily and top_keys:
            from app.integrations.tavily import web_search

            mb = _month_name(b)
            _status(run_id, f"Researching company changes: {', '.join(top_keys[:3])}")
            t0 = time.perf_counter()
            for sym in top_keys[:3]:
                q = f"{sym} stock {mb} earnings guidance news"
                res = await asyncio.to_thread(web_search, q, 3) or []
                if not res:
                    continue
                top = res[0]
                text = f"{str(top.get('title','')).strip()} — {str(top.get('content','')).strip()[:160]}"
                company.append({
                    "symbol": sym,
                    "text": text.strip(" —"),
                    "sources": [r["url"] for r in res if r.get("url")][:3],
                })
            ms = int((time.perf_counter() - t0) * 1000)
            bus.publish(run_id, {"type": "tool_call", "name": "company_context",
                                 "input": {"symbols": top_keys[:3], "month": mb, "max_results": 3}})
            bus.publish(run_id, {"type": "tool_result", "name": "company_context",
                                 "summary": f"{len(company)} companies: "
                                            f"{', '.join(c['symbol'] for c in company)}"[:300]})
            traj.add(step_type="tool_call", label="company_context", tool_name="company_context",
                     input_summary={"symbols": top_keys[:3]}, duration_ms=ms,
                     output_summary=_summarize([c["symbol"] for c in company], 200))
        elif not settings.has_tavily:
            _status(run_id, "Tavily key not set — skipping company context")

        # ---- the report is computed deterministically FIRST; the model only narrates it ----
        compare_for_fb = dict(compare)
        compare_for_fb["_positions"] = positions
        compare_for_fb["_lots_by_driver"] = lots_by_driver
        report = fallback_mod.build_fallback_report(compare_for_fb, prior, web, macro)
        report["company_changes"] = [dict(c) for c in company]
        for c in company:
            for u in c.get("sources", []):
                if u not in report["sources"]:
                    report["sources"].append(u)

        # ---- narrative layer: ONE small, fast LLM call over the finished numbers ----
        context_insights = [i for i in prior if i.get("kind") == "context"]
        advice_insights = [i for i in prior if i.get("kind") != "context"]
        user_msg = build_narrative_message(
            period_a=a, period_b=b, compare=compare, prior_insights=advice_insights,
            context_insights=context_insights, macro=macro, company=company, question=question,
        )
        llm_meta: dict[str, Any] = {}
        if llm.available():
            _status(run_id, f"Narrative layer from {settings.llm_model}")
            bus.publish(run_id, {"type": "tool_call", "name": "llm_narrative",
                                 "input": {"model": settings.llm_model, "chars": len(user_msg)}})
            parsed, llm_meta = await asyncio.to_thread(
                llm.chat_json, NARRATIVE_SYSTEM_PROMPT, user_msg, 320, 0.2
            )
            model_used = llm_meta.get("model") or settings.llm_model or "llm"
            secs = (llm_meta.get("latency_ms") or 0) / 1000.0
            merged = _merge_narrative(report, parsed) if parsed else False
            if merged:
                used_fallback = False
                _status(run_id, f"Narrative layer from local model ({secs:.0f} s)")
                bus.publish(run_id, {"type": "tool_result", "name": "llm_narrative",
                                     "summary": str(report.get("headline"))[:300]})
            else:
                used_fallback = True
                _status(run_id, f"Narrative layer fallback ({secs:.0f} s) — {llm_meta.get('error')}")
                bus.publish(run_id, {"type": "tool_result", "name": "llm_narrative",
                                     "summary": f"unusable model output ({llm_meta.get('error')}) — deterministic narrative"[:300]})
            traj.add(step_type="reasoning", label="llm_narrative", tool_name="llm_narrative",
                     input_summary={"model": model_used, "prompt_chars": len(user_msg)},
                     output_summary=str(report.get("headline", ""))[:500],
                     duration_ms=int(llm_meta.get("latency_ms") or 0),
                     token_count=int(llm_meta.get("tokens_in") or 0) + int(llm_meta.get("tokens_out") or 0),
                     status="success" if not used_fallback else "error")
        else:
            _status(run_id, "No LLM configured — deterministic narrative")
            used_fallback = True
            traj.add(step_type="reasoning", label="deterministic_fallback",
                     tool_name="build_fallback_report",
                     input_summary={"facts": len(compare.get("facts") or [])},
                     output_summary=str(report.get("headline", ""))[:500], duration_ms=0)

        # ---- remember what we learned ----
        saved = 0
        try:
            ev_head = [d.get("name") for d in (report.get("drivers") or [])[:3] if d.get("name")]
            memory.save_insight(run_id, "observation", str(report.get("headline", ""))[:800], ev_head)
            saved += 1
            for adv in (report.get("advisements") or [])[:5]:
                memory.save_insight(run_id, "advisement",
                                    f"{adv.get('title')} — {adv.get('detail')}"[:800],
                                    list(adv.get("evidence") or []))
                saved += 1
            saved += _save_company_context(run_id, b, report.get("company_changes") or [])
        except Exception as e:
            log.warning("saving insights failed: %s", e)
        traj.add(step_type="tool_call", label="save_insight", tool_name="save_insight",
                 input_summary={"count": saved}, output_summary=f"{saved} insights persisted")

        latency_ms = int((time.perf_counter() - t_start) * 1000)

        _persist_run(run_id, status="done", report_json=json.dumps(report, default=str),
                     model=model_used, latency_ms=latency_ms, prism_session_id=run_id, error=None)

        # ---- PRISM ----
        traced = False
        try:
            meta = {
                "session_id": run_id,
                "period_a": a,
                "period_b": b,
                "fallback": used_fallback,
                "question": question or "",
                "advisements": len(report.get("advisements") or []),
            }
            traced_llm = await asyncio.to_thread(
                lambda: prism.trace_llm(
                    model=model_used,
                    input_messages=[{"role": "system", "content": NARRATIVE_SYSTEM_PROMPT[:2000]},
                                    {"role": "user", "content": user_msg[:8000]}],
                    output=json.dumps(report, default=str)[:8000],
                    latency_ms=int(llm_meta.get("latency_ms") or latency_ms),
                    run_id=run_id,
                    tokens_in=int(llm_meta.get("tokens_in") or 0),
                    tokens_out=int(llm_meta.get("tokens_out") or 0),
                    metadata=meta,
                )
            )
            traj.add(step_type="final_answer", label="submit_report",
                     input_summary={"fallback": used_fallback},
                     output_summary=str(report.get("headline", ""))[:500],
                     duration_ms=latency_ms)
            traced_traj = await asyncio.to_thread(
                prism.submit_trajectory, traj.steps, run_id, model_used, "success"
            )
            await asyncio.to_thread(prism.flush, 3.0)
            traced = bool(traced_llm or traced_traj)
        except Exception as e:
            log.warning("PRISM tracing failed: %s", e)
            traced = False

        bus.publish(run_id, {
            "type": "final",
            "run_id": run_id,
            "report": report,
            "prism": {"session_id": run_id, "trajectory_url": None, "traced": traced},
            "model": model_used,
            "latency_ms": latency_ms,
            "fallback": used_fallback,
        })
        bus.persist(run_id)
        return report

    except Exception as e:  # noqa: BLE001 - the run must never crash the server
        log.exception("run %s failed", run_id)
        latency_ms = int((time.perf_counter() - t_start) * 1000)
        try:
            _persist_run(run_id, status="error", error=str(e)[:500], latency_ms=latency_ms,
                         prism_session_id=run_id, model=model_used)
        except Exception:
            pass
        try:
            traj.add(step_type="final_answer", label="error", output_summary=str(e)[:300], status="error")
            await asyncio.to_thread(prism.submit_trajectory, traj.steps, run_id, model_used, "error")
            await asyncio.to_thread(prism.flush, 2.0)
        except Exception:
            pass
        bus.publish(run_id, {"type": "error", "message": str(e)[:500]})
        bus.persist(run_id)
        return {}
