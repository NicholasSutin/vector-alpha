"""Prompts for the portfolio variance analyst ("Explain the Change")."""
from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """You are Vector Alpha, a portfolio variance analyst.

Your job is to explain WHAT CHANGED between two monthly periods of a trading book, WHY it
changed, and WHAT THE TRADER SHOULD DO ABOUT IT. You are given pre-computed, trustworthy
analytics: topline deltas, ranked drivers with contribution percentages, closed-lot evidence,
behaviour flags, positions, prior insights from earlier runs, and optional news snippets.

The bar you must clear:
  WEAK : "P&L fell 63%."
  GOOD : "P&L fell 63% (-$2,140), primarily driven by NVDA earnings-week calls (71% of the
          decline, 3 lots: lot_a1, lot_a2, lot_a3), amplified by over-trading (trade count
          +85%, average hold time 9d -> 2d)."

RULES
1. EVERY claim cites evidence: closed-lot ids (lot_*), transaction ids, or a fact index like
   "fact#3". Put those strings in the `evidence` arrays. Never invent an id — only use ids that
   appear in the data given to you.
2. Never invent numbers. Every figure must come from the supplied facts, topline or drivers.
   If a number is not in the data, do not state it.
3. Review the PRIOR INSIGHTS. For each one decide: was it followed? did it help? Compare period
   B against period A on the metric the insight was about (e.g. a "reduce concentration" advice
   is `validated` if concentration_top_share fell, `invalidated` if it rose, `unclear` if you
   cannot tell). Put one entry per prior insight in `prior_insight_review`, reusing its exact
   `insight_id`.
4. Advisements must be CONCRETE and MEASURABLE — a rule you could enforce with code. Good:
   "Cap single-underlying exposure at 30% of monthly buys", "No new option opens within 5
   trading days of an earnings date", "Minimum planned hold of 5 days for swing-tagged trades",
   "Cut monthly trade count below 40 to keep fees under $150". Bad: "be more careful",
   "diversify more", "manage risk".
5. `proposed_trades` are OPTIONAL, small, PAPER-ONLY, and each must be tied to an advisement.
   Only propose a trade you can justify from the positions list (do not propose selling
   something not held). order_type is "MKT", qty a small integer (1-3). Zero trades is fine.
6. Behaviour observations go in `behaviour` (over-trading, hold-time collapse, concentration,
   options drift, fee drag).
7. MARKET CONTEXT: if a MARKET CONTEXT block is supplied, fill `market_context` with 3-6 bullets
   describing the market/macro regime of period B (index moves, volatility, rates/inflation,
   sector or single-name news) and how it interacts with this book. Every bullet MUST end with
   "[source: <url>]" using a url from the supplied snippets, and every such url must also appear
   in `sources`. If no MARKET CONTEXT block is supplied, return an empty list.
8. Output ONLY a single JSON object. No prose before or after. No markdown code fences.

OUTPUT SCHEMA (exact keys, exact types):
{
  "headline": "string - one sentence, the 'X fell 63%, driven by ...' style",
  "what_changed": ["string bullet with numbers and evidence ids"],
  "why": ["string bullet explaining causation"],
  "drivers": [{"name": "NVDA", "contribution_pct": 0.71, "detail": "3 earnings-week call lots",
               "evidence": ["lot_a1", "lot_a2"]}],
  "behaviour": ["trade count +85% (42 -> 78); avg hold 9d -> 2d"],
  "advisements": [{"title": "Cap single-underlying exposure at 30% of monthly buys",
                   "detail": "NVDA was 61% of gross bought in 2026-07 ...",
                   "priority": "high", "evidence": ["fact#1", "lot_a1"]}],
  "proposed_trades": [{"id": "pt_1", "symbol": "NVDA", "side": "SELL", "qty": 2,
                       "order_type": "MKT", "limit_price": null, "tif": "DAY",
                       "rationale": "cut concentration per advisement 1"}],
  "prior_insight_review": [{"insight_id": "i_abc123", "text": "reduce NVDA concentration",
                            "verdict": "validated", "note": "top share 0.61 -> 0.38"}],
  "confidence": 0.72,
  "sources": ["https://..."],
  "market_context": ["Nasdaq fell 4.2% in July as AI names sold off [source: https://...]"]
}

`priority` is one of "high" | "medium" | "low".
`verdict` is one of "followed" | "ignored" | "validated" | "invalidated" | "unclear".
`side` is "BUY" or "SELL". `tif` is "DAY" or "GTC". `confidence` is a number 0..1.
Aim for 3-6 what_changed, 2-5 why, 3-5 advisements. BE TERSE: keep the whole JSON under 350
words — one or two sentences per bullet. Return ONLY the JSON object.
"""


def _short(obj: Any, limit: int = 4000) -> str:
    try:
        s = json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return s[:limit]


def build_user_message(
    *,
    period_a: str,
    period_b: str,
    compare: dict[str, Any],
    lots_by_driver: dict[str, list[dict[str, Any]]],
    positions: list[dict[str, Any]],
    prior_insights: list[dict[str, Any]],
    web: list[dict[str, Any]],
    macro: list[dict[str, Any]] | None = None,
    question: str | None = None,
) -> str:
    facts = compare.get("facts") or []
    topline = compare.get("topline") or []
    drivers = (compare.get("drivers") or [])[:8]
    flags = compare.get("behaviour_flags") or []
    sa = compare.get("a") or {}
    sb = compare.get("b") or {}

    def summ(s: dict) -> dict:
        keys = ("period", "realized_pnl", "fees", "trade_count", "closed_lots", "win_rate",
                "avg_win", "avg_loss", "avg_hold_days", "options_share", "turnover",
                "concentration_top_symbol", "concentration_top_share")
        return {k: s.get(k) for k in keys if k in s}

    parts: list[str] = []
    parts.append(f"PERIOD A = {period_a}   PERIOD B = {period_b} (B is the period to explain)")
    parts.append("")
    parts.append("FACTS (cite as fact#N, 1-indexed):")
    for i, f in enumerate(facts[:15], 1):
        parts.append(f"  fact#{i}: {f}")
    parts.append("")
    parts.append("TOPLINE DELTAS:")
    for t in topline[:10]:
        parts.append(
            f"  {t.get('label', t.get('metric'))}: {t.get('a')} -> {t.get('b')} "
            f"(delta {t.get('delta')}, pct {t.get('pct')})"
        )
    parts.append("")
    parts.append("PERIOD SUMMARIES:")
    parts.append(f"  A: {_short(summ(sa), 1200)}")
    parts.append(f"  B: {_short(summ(sb), 1200)}")
    parts.append("")
    parts.append("RANKED DRIVERS (contribution_pct of the realized P&L delta):")
    for d in drivers:
        parts.append(
            f"  [{d.get('dimension')}] {d.get('key')}: a={d.get('a')} b={d.get('b')} "
            f"delta={d.get('delta')} contribution_pct={d.get('contribution_pct')} "
            f"note={d.get('note','')} evidence_lot_ids={_short(d.get('evidence_lot_ids', [])[:6], 400)}"
        )
    parts.append("")
    if lots_by_driver:
        parts.append("DRILL-DOWN — closed lots in period B for the top drivers:")
        for key, lots in lots_by_driver.items():
            parts.append(f"  {key}:")
            for lot in lots[:5]:
                parts.append(
                    f"    {lot.get('lot_id')} {lot.get('symbol')} "
                    f"open {str(lot.get('open_ts',''))[:10]} -> close {str(lot.get('close_ts',''))[:10]} "
                    f"qty {lot.get('qty')} pnl {lot.get('realized_pnl')} hold {lot.get('hold_days')}d "
                    f"tag {lot.get('strategy_tag','')}"
                )
        parts.append("")
    parts.append("BEHAVIOUR FLAGS: " + ("; ".join(str(f) for f in flags) if flags else "(none)"))
    parts.append("")
    parts.append("CURRENT POSITIONS (only these may appear in proposed_trades):")
    if positions:
        for p in positions[:25]:
            parts.append(
                f"  {p.get('symbol')} ({p.get('asset_type')}) qty {p.get('qty')} avg_cost {p.get('avg_cost')}"
            )
    else:
        parts.append("  (none — propose no trades)")
    parts.append("")
    parts.append("PRIOR INSIGHTS from earlier runs (review every one):")
    if prior_insights:
        for pi in prior_insights[:10]:
            parts.append(
                f"  insight_id={pi.get('id')} kind={pi.get('kind')} status={pi.get('status')} "
                f"created={pi.get('created_at')} text={pi.get('text')}"
            )
    else:
        parts.append("  (none — this is the first run; return an empty prior_insight_review)")
    parts.append("")
    if web:
        parts.append("WEB SNIPPETS — driver-specific news (cite the urls in `sources`):")
        for w in web[:4]:
            parts.append(f"  - {w.get('title')} :: {w.get('url')} :: {str(w.get('content',''))[:300]}")
        parts.append("")
    if macro:
        parts.append("MARKET CONTEXT — market/macro regime during period B. Use these to fill")
        parts.append("`market_context` with 3-6 bullets, each ending in \"[source: <url>]\":")
        for w in macro[:8]:
            parts.append(f"  - {w.get('title')} :: {w.get('url')} :: {str(w.get('content',''))[:300]}")
        parts.append("")
    if question:
        parts.append(f"USER QUESTION (answer it inside the report): {question}")
        parts.append("")
    parts.append("Return ONLY the JSON object described in the schema.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Fast narrative layer
# ---------------------------------------------------------------------------
# A 9B model at ~8 tok/s cannot write a whole Report inside any sane timeout, so the engine
# computes the report deterministically and the model only writes the prose over the top.
NARRATIVE_SYSTEM_PROMPT = """You are a portfolio variance analyst. The numbers are already
computed and correct — do NOT recompute or invent any. Write only the narrative layer.

Return ONLY this JSON object, nothing else:
{"headline": "one sentence naming the biggest driver and the behaviour that amplified it",
 "why": ["3-4 short causal bullets"],
 "behaviour": ["2-3 trading-behaviour observations"],
 "market_context": ["0-4 bullets on the market/macro regime, each ending [source: url]"],
 "company_changes": [{"symbol": "NVDA", "text": "what changed at this company, one sentence"}]}

Rules: use only numbers, ids and urls that appear below. Cite lot ids where natural.
Keep the WHOLE response under 120 words. No prose outside the JSON. No code fences."""


def _clip(s: Any, n: int) -> str:
    t = str(s or "").replace("\n", " ").strip()
    return t[:n]


def build_narrative_message(
    *,
    period_a: str,
    period_b: str,
    compare: dict[str, Any],
    prior_insights: list[dict[str, Any]],
    context_insights: list[dict[str, Any]],
    macro: list[dict[str, Any]],
    company: list[dict[str, Any]],
    question: str | None = None,
    budget: int = 3000,
) -> str:
    """Compact (<= `budget` chars) user message for the narrative-only LLM call."""
    facts = (compare.get("facts") or [])[:8]
    drivers = (compare.get("drivers") or [])[:4]
    flags = compare.get("behaviour_flags") or []

    p: list[str] = [f"PERIOD {period_a} -> {period_b} (explain {period_b}).", "FACTS:"]
    p += [f"{i}. {_clip(f, 160)}" for i, f in enumerate(facts, 1)]
    p.append("DRIVERS:")
    for d in drivers:
        p.append(
            f"- {d.get('key')} ({d.get('dimension')}) {d.get('a')}->{d.get('b')} "
            f"contrib {d.get('contribution_pct')} lots {','.join((d.get('evidence_lot_ids') or [])[:3])}"
        )
    p.append("BEHAVIOUR: " + _clip("; ".join(str(f) for f in flags), 240))
    if prior_insights:
        p.append("PRIOR ADVICE:")
        p += [f"- {_clip(pi.get('text'), 110)}" for pi in prior_insights[:3]]
    if context_insights:
        p.append("KNOWN BUSINESS CONTEXT (from earlier runs):")
        p += [f"- {_clip(ci.get('text'), 110)}" for ci in context_insights[:3]]
    if macro:
        p.append("MARKET NEWS:")
        p += [f"- {_clip(m.get('title'), 90)} :: {m.get('url')} :: {_clip(m.get('content'), 200)}"
              for m in macro[:3]]
    if company:
        p.append("COMPANY NEWS (write one `company_changes` entry per symbol):")
        for c in company[:3]:
            p.append(f"- {c.get('symbol')}: {_clip(c.get('text'), 200)}")
    if question:
        p.append(f"USER ASKS: {_clip(question, 160)}")
    p.append("Return ONLY the JSON object. Under 120 words.")

    msg = "\n".join(p)
    if len(msg) > budget:            # hard cap: drop from the end, keep the header intact
        msg = msg[: budget - 60].rsplit("\n", 1)[0] + "\nReturn ONLY the JSON object. Under 120 words."
    return msg
