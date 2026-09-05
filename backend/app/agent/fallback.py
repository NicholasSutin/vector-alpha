"""Deterministic report builder — used when no LLM is configured or the LLM fails.

Same `models.Report` shape as the model path, so the UI and the demo never degrade.
"""
from __future__ import annotations

from typing import Any


def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "n/a"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(float(v)):,.0f}"


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{float(v) * 100:.0f}%"


def _get(d: dict, key: str, default: Any = 0) -> Any:
    v = d.get(key, default)
    return default if v is None else v


def _topline(compare: dict, metric: str) -> dict | None:
    for t in compare.get("topline") or []:
        if t.get("metric") == metric:
            return t
    return None


def _delta_pct(a: float, b: float) -> float | None:
    if not a:
        return None
    return (b - a) / abs(a)


def build_fallback_report(
    compare: dict[str, Any],
    prior_insights: list[dict[str, Any]] | None = None,
    web: list[dict[str, Any]] | None = None,
    macro: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    prior_insights = prior_insights or []
    web = web or []
    macro = macro or []
    sa: dict = compare.get("a") or {}
    sb: dict = compare.get("b") or {}
    facts: list[str] = list(compare.get("facts") or [])
    drivers: list[dict] = list(compare.get("drivers") or [])
    flags: list[str] = list(compare.get("behaviour_flags") or [])
    topline: list[dict] = list(compare.get("topline") or [])
    positions: list[dict] = list(compare.get("_positions") or [])

    pa = _get(sa, "period", "A")
    pb = _get(sb, "period", "B")

    # ---------------- headline ----------------
    pnl = _topline(compare, "realized_pnl")
    if pnl is None and topline:
        pnl = max(topline, key=lambda t: abs(float(_get(t, "delta", 0))))
    top_driver = drivers[0] if drivers else None

    if pnl is not None:
        direction = "fell" if float(_get(pnl, "delta", 0)) < 0 else "rose"
        head = (
            f"{_get(pnl, 'label', 'Realized P&L')} {direction} "
            f"{_fmt_usd(abs(float(_get(pnl, 'delta', 0))))} "
            f"({_fmt_usd(_get(pnl, 'a', 0))} → {_fmt_usd(_get(pnl, 'b', 0))}"
            + (f", {_pct(_get(pnl, 'pct'))}" if _get(pnl, "pct", None) is not None else "")
            + f") from {pa} to {pb}"
        )
    else:
        head = f"Period {pb} vs {pa}"
    if top_driver:
        head += (
            f", primarily driven by {top_driver.get('key')} "
            f"({_pct(_get(top_driver, 'contribution_pct', 0))} of the move, "
            f"{len(top_driver.get('evidence_lot_ids') or [])} lots)"
        )
    if flags:
        head += f", amplified by {flags[0]}"
    headline = head + "."

    # ---------------- what changed / why / behaviour ----------------
    what_changed = [f"{f} [fact#{i}]" for i, f in enumerate(facts[:6], 1)]
    if not what_changed:
        for t in topline[:5]:
            what_changed.append(
                f"{t.get('label')}: {_fmt_usd(_get(t,'a',0))} → {_fmt_usd(_get(t,'b',0))} "
                f"(delta {_fmt_usd(_get(t,'delta',0))})"
            )

    why: list[str] = []
    report_drivers: list[dict] = []
    for d in drivers[:5]:
        ev = list(d.get("evidence_lot_ids") or [])[:5]
        why.append(
            f"{d.get('key')} ({d.get('dimension')}) moved {_fmt_usd(_get(d,'delta',0))} "
            f"({_fmt_usd(_get(d,'a',0))} → {_fmt_usd(_get(d,'b',0))}), "
            f"{_pct(_get(d,'contribution_pct',0))} of the total change"
            + (f" — {d.get('note')}" if d.get("note") else "")
            + (f" [lots: {', '.join(ev)}]" if ev else "")
        )
        report_drivers.append(
            {
                "name": str(d.get("key", "")),
                "contribution_pct": float(_get(d, "contribution_pct", 0)),
                "detail": str(d.get("note") or
                              f"{_fmt_usd(_get(d,'a',0))} → {_fmt_usd(_get(d,'b',0))} "
                              f"(delta {_fmt_usd(_get(d,'delta',0))})"),
                "evidence": ev,
            }
        )
    for fl in flags[:4]:
        why.append(f"Behaviour shift: {fl}")

    behaviour = list(flags)
    conc_b = float(_get(sb, "concentration_top_share", 0))
    if conc_b:
        behaviour.append(
            f"Concentration: {_get(sb,'concentration_top_symbol','')} was "
            f"{_pct(conc_b)} of gross bought in {pb} (was {_pct(_get(sa,'concentration_top_share',0))} in {pa})"
        )

    # ---------------- advisements (rules over the summaries) ----------------
    advisements: list[dict] = []
    ev_facts = [f"fact#{i}" for i in range(1, min(len(facts), 3) + 1)]

    top_sym = str(_get(sb, "concentration_top_symbol", "") or "")
    if conc_b > 0.4:
        advisements.append({
            "title": "Cap single-underlying exposure at 30% of monthly buys",
            "detail": (f"{top_sym or 'The top name'} was {_pct(conc_b)} of gross bought in {pb}. "
                       f"Hard-cap any one underlying at 30% of the month's gross buys."),
            "priority": "high",
            "evidence": ev_facts + ((top_driver.get("evidence_lot_ids") or [])[:3] if top_driver else []),
        })

    opt_a, opt_b = float(_get(sa, "options_share", 0)), float(_get(sb, "options_share", 0))
    if opt_b - opt_a > 0.2:
        advisements.append({
            "title": "Limit options to 40% of trades per month",
            "detail": (f"Options share rose {_pct(opt_a)} → {_pct(opt_b)} between {pa} and {pb}. "
                       f"Cap option trades at 40% of monthly trade count until win rate recovers."),
            "priority": "high",
            "evidence": ev_facts,
        })

    hold_a, hold_b = float(_get(sa, "avg_hold_days", 0)), float(_get(sb, "avg_hold_days", 0))
    if hold_a > 0 and hold_b < hold_a * 0.5:
        advisements.append({
            "title": "Minimum planned hold of 5 days for swing-tagged trades",
            "detail": (f"Average hold time collapsed {hold_a:.1f}d → {hold_b:.1f}d. Any trade tagged "
                       f"'swing' must have a 5-trading-day minimum planned hold before entry."),
            "priority": "medium",
            "evidence": ev_facts,
        })

    wr_a, wr_b = float(_get(sa, "win_rate", 0)), float(_get(sb, "win_rate", 0))
    loss_a, loss_b = abs(float(_get(sa, "avg_loss", 0))), abs(float(_get(sb, "avg_loss", 0)))
    if (wr_a - wr_b) > 0.15 and loss_b > loss_a:
        advisements.append({
            "title": "Hard stop-loss at 1.0x average win per position",
            "detail": (f"Win rate fell {_pct(wr_a)} → {_pct(wr_b)} while the average loss widened "
                       f"{_fmt_usd(loss_a)} → {_fmt_usd(loss_b)}. Set a hard stop at "
                       f"{_fmt_usd(abs(float(_get(sb,'avg_win',0))) or loss_a)} per position."),
            "priority": "high",
            "evidence": ev_facts,
        })

    fee_a, fee_b = abs(float(_get(sa, "fees", 0))), abs(float(_get(sb, "fees", 0)))
    if fee_a > 0 and (fee_b - fee_a) / fee_a > 0.4:
        advisements.append({
            "title": f"Reduce turnover — keep monthly fees under {_fmt_usd(fee_a * 1.1)}",
            "detail": (f"Fees rose {_fmt_usd(fee_a)} → {_fmt_usd(fee_b)} "
                       f"(+{_pct((fee_b - fee_a) / fee_a)}) on {int(_get(sb,'trade_count',0))} trades. "
                       f"Cap monthly trade count at {max(1, int(int(_get(sa,'trade_count',0)) * 1.1))}."),
            "priority": "medium",
            "evidence": ev_facts,
        })

    strategies = {str(s.get("key", "")).lower(): s for s in (sb.get("by_strategy") or [])}
    earnings = strategies.get("earnings")
    if earnings is not None and float(_get(earnings, "realized_pnl", 0)) < 0:
        advisements.append({
            "title": "No new option opens within 5 trading days of an earnings date",
            "detail": (f"Earnings-tagged trades lost {_fmt_usd(_get(earnings,'realized_pnl',0))} in {pb} "
                       f"across {int(_get(earnings,'trades',0))} trades. Block new option opens inside "
                       f"the 5 trading days before any earnings report."),
            "priority": "high",
            "evidence": ev_facts,
        })

    tc_a, tc_b = int(_get(sa, "trade_count", 0)), int(_get(sb, "trade_count", 0))
    if len(advisements) < 3 and tc_a and tc_b > tc_a * 1.4:
        advisements.append({
            "title": f"Cap monthly trade count at {max(1, int(tc_a * 1.1))}",
            "detail": f"Trade count rose {tc_a} → {tc_b} ({_pct(_delta_pct(tc_a, tc_b))}) between {pa} and {pb}.",
            "priority": "medium",
            "evidence": ev_facts,
        })
    while len(advisements) < 3:
        generic = [
            {
                "title": "Log a written thesis and exit level before every entry",
                "detail": f"No pre-trade thesis is recorded for the {tc_b} trades in {pb}; require one per entry.",
                "priority": "medium",
                "evidence": ev_facts,
            },
            {
                "title": "Review the top 3 losing lots every month before adding risk",
                "detail": f"Run this variance report at each month end ({pa} → {pb}) and act on the top driver.",
                "priority": "low",
                "evidence": ev_facts,
            },
            {
                "title": "Keep any single position under 10% of account equity",
                "detail": "Position sizing rule to bound single-name drawdown.",
                "priority": "low",
                "evidence": ev_facts,
            },
        ]
        nxt = generic[len(advisements) % len(generic)]
        if any(a["title"] == nxt["title"] for a in advisements):
            break
        advisements.append(nxt)
    advisements = advisements[:5]

    # ---------------- proposed trades (at most one, from concentration) ----------------
    proposed: list[dict] = []
    if conc_b > 0.4 and top_sym:
        held = None
        for p in positions:
            if str(p.get("symbol", "")).upper() == top_sym.upper() and float(_get(p, "qty", 0)) > 0:
                held = p
                break
        if held is not None:
            qty = max(1, min(3, int(abs(float(_get(held, "qty", 1))) // 3) or 1))
            proposed.append({
                "id": "pt_1",
                "symbol": top_sym,
                "side": "SELL",
                "qty": qty,
                "order_type": "MKT",
                "limit_price": None,
                "tif": "DAY",
                "rationale": (f"Cut concentration: {top_sym} was {_pct(conc_b)} of gross bought in {pb} "
                              f"(advisement: cap single-underlying exposure at 30%). Paper only."),
            })

    # ---------------- prior insight review ----------------
    review: list[dict] = []
    for pi in prior_insights[:15]:
        text = str(pi.get("text", ""))
        low = text.lower()
        verdict, note = "unclear", "No measurable metric tied to this insight in period " + str(pb) + "."
        if "concentration" in low or "single-underlying" in low or "exposure" in low:
            ca, cb = float(_get(sa, "concentration_top_share", 0)), conc_b
            if cb < ca:
                verdict, note = "validated", f"Top-symbol share fell {_pct(ca)} → {_pct(cb)}."
            elif cb > ca:
                verdict, note = "invalidated", f"Top-symbol share rose {_pct(ca)} → {_pct(cb)}."
        elif "option" in low:
            if opt_b < opt_a:
                verdict, note = "validated", f"Options share fell {_pct(opt_a)} → {_pct(opt_b)}."
            elif opt_b > opt_a:
                verdict, note = "invalidated", f"Options share rose {_pct(opt_a)} → {_pct(opt_b)}."
        elif "hold" in low:
            if hold_b > hold_a:
                verdict, note = "validated", f"Average hold rose {hold_a:.1f}d → {hold_b:.1f}d."
            elif hold_b < hold_a:
                verdict, note = "invalidated", f"Average hold fell {hold_a:.1f}d → {hold_b:.1f}d."
        elif "trade count" in low or "turnover" in low or "over-trad" in low or "overtrad" in low:
            if tc_b < tc_a:
                verdict, note = "validated", f"Trade count fell {tc_a} → {tc_b}."
            elif tc_b > tc_a:
                verdict, note = "invalidated", f"Trade count rose {tc_a} → {tc_b}."
        elif "fee" in low:
            if fee_b < fee_a:
                verdict, note = "validated", f"Fees fell {_fmt_usd(fee_a)} → {_fmt_usd(fee_b)}."
            elif fee_b > fee_a:
                verdict, note = "invalidated", f"Fees rose {_fmt_usd(fee_a)} → {_fmt_usd(fee_b)}."
        elif "stop" in low or "loss" in low:
            if loss_b < loss_a:
                verdict, note = "validated", f"Average loss narrowed {_fmt_usd(loss_a)} → {_fmt_usd(loss_b)}."
            elif loss_b > loss_a:
                verdict, note = "invalidated", f"Average loss widened {_fmt_usd(loss_a)} → {_fmt_usd(loss_b)}."
        review.append({
            "insight_id": str(pi.get("id", "")),
            "text": text,
            "verdict": verdict,
            "note": note,
        })

    # ---------------- market context + sources ----------------
    market_context: list[str] = []
    for w in (macro or [])[:5]:
        url = str(w.get("url", ""))
        title = str(w.get("title", "")).strip()
        body = str(w.get("content", "")).strip()[:140]
        if not url:
            continue
        market_context.append(f"{title}: {body} [source: {url}]")

    sources = [w.get("url") for w in list(web) + list(macro) if w.get("url")]
    seen: set[str] = set()
    sources = [u for u in sources if not (u in seen or seen.add(u))]

    for w in web[:3]:
        why.append(f"Market context: {w.get('title')} ({w.get('url')})")

    return {
        "headline": headline,
        "what_changed": what_changed,
        "why": why[:8],
        "drivers": report_drivers,
        "behaviour": behaviour[:8],
        "advisements": advisements,
        "proposed_trades": proposed,
        "prior_insight_review": review,
        "confidence": 0.6,
        "sources": sources,
        "market_context": market_context,
    }
