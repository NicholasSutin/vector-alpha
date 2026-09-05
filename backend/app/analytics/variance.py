"""Period-over-period variance: topline deltas, ranked drivers, bridge, facts.

`compare_periods(a, b, lots, txns)` returns a models.VarianceReport dict.
`facts` is the evidence layer the LLM reasons over (and the deterministic
fallback narrates), so every sentence must be numerically derivable from the
two summaries handed in.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from app.analytics.periods import period_of

DIMENSIONS = (
    ("underlying", "by_underlying"),
    ("asset_type", "by_asset_type"),
    ("strategy", "by_strategy"),
    ("weekday", "by_weekday"),
)

TOPLINE_SPEC = (
    ("realized_pnl", "Realized P&L", "usd"),
    ("fees", "Fees", "usd"),
    ("dividends", "Dividends", "usd"),
    ("net_deposits", "Net deposits", "usd"),
    ("trade_count", "Trades", "int"),
    ("win_rate", "Win rate", "pct"),
    ("avg_hold_days", "Avg hold", "days"),
    ("options_share", "Options share", "pct"),
    ("turnover", "Turnover", "usd"),
    ("concentration_top_share", "Top-symbol concentration", "pct"),
    ("expectancy", "Expectancy / trade", "usd"),
)


# ---------------------------------------------------------------- formatting

def usd(x: float) -> str:
    """-1240.4 -> '−$1,240'  (unicode minus, no cents above $10)."""
    v = float(x or 0)
    sign = "−" if v < 0 else ""
    a = abs(v)
    body = f"{a:,.0f}" if a >= 10 else f"{a:,.2f}"
    return f"{sign}${body}"


def pct(x: float | None, digits: int = 0) -> str:
    if x is None:
        return "n/a"
    v = float(x) * 100.0
    return f"{v:+.{digits}f}%" if digits else f"{v:+.0f}%"


def pct_plain(x: float, digits: int = 0) -> str:
    return f"{float(x) * 100:.{digits}f}%"


def signed(x: float) -> str:
    """'+$420' / '−$1,240' — always carries an explicit sign."""
    return usd(x) if float(x or 0) < 0 else "+" + usd(x)


def pct_change(a: float, b: float) -> float | None:
    """Relative change; None when the base is ~0 (undefined)."""
    a, b = float(a or 0), float(b or 0)
    if abs(a) < 1e-9:
        return None
    return round((b - a) / abs(a), 4)


def _f(d: dict, key: str) -> float:
    try:
        return float(d.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- drivers

def _driver_map(summary: dict, field: str) -> dict[str, float]:
    return {str(d.get("key")): float(d.get("realized_pnl") or 0) for d in summary.get(field) or []}


def _denominator(a: dict, b: dict) -> float:
    """Total realized-P&L delta, with a sane guard when it is ~zero."""
    total = _f(b, "realized_pnl") - _f(a, "realized_pnl")
    if abs(total) < 1e-6:
        return max(abs(_f(a, "realized_pnl")), abs(_f(b, "realized_pnl")), 1.0)
    return total


def _lot_key(lot: dict, dimension: str) -> str:
    if dimension == "underlying":
        return str(lot.get("underlying") or "?").upper()
    if dimension == "asset_type":
        return str(lot.get("asset_type") or "other")
    if dimension == "strategy":
        return str(lot.get("strategy_tag") or "") or "untagged"
    if dimension == "weekday":
        from app.analytics.periods import _weekday_of
        return _weekday_of(str(lot.get("open_ts") or ""))
    return "?"


def _note_for(key: str, lots: Sequence[dict]) -> str:
    """'3 lots, largest NVDA 2026-07-18 C 150 −$1,240, avg hold 2.1d'."""
    if not lots:
        return f"{key} had no closed lots in the later period"
    worst = max(lots, key=lambda l: abs(float(l.get("realized_pnl") or 0)))
    hold = sum(float(l.get("hold_days") or 0) for l in lots) / len(lots)
    n = len(lots)
    return (f"{n} lot{'s' if n != 1 else ''}, largest {worst.get('symbol')} "
            f"{usd(worst.get('realized_pnl'))}, avg hold {hold:.1f}d")


def _build_drivers(a: dict, b: dict, lots: Sequence[dict], limit: int = 12) -> list[dict[str, Any]]:
    denom = _denominator(a, b)
    period_b = b.get("period")
    lots_b = [l for l in lots if period_of(str(l.get("close_ts") or "")) == period_b]

    by_dim_lots: dict[str, dict[str, list[dict]]] = {}
    for dim, _field in DIMENSIONS:
        bucket: dict[str, list[dict]] = defaultdict(list)
        for lot in lots_b:
            bucket[_lot_key(lot, dim)].append(lot)
        by_dim_lots[dim] = bucket

    out: list[dict[str, Any]] = []
    for dim, field in DIMENSIONS:
        amap, bmap = _driver_map(a, field), _driver_map(b, field)
        for key in set(amap) | set(bmap):
            av, bv = amap.get(key, 0.0), bmap.get(key, 0.0)
            delta = round(bv - av, 2)
            if abs(delta) < 0.005:
                continue
            key_lots = sorted(by_dim_lots[dim].get(key, []),
                              key=lambda l: -abs(float(l.get("realized_pnl") or 0)))
            out.append({
                "dimension": dim,
                "key": key,
                "a": round(av, 2),
                "b": round(bv, 2),
                "delta": delta,
                "contribution_pct": round(delta / denom, 4),
                "evidence_lot_ids": [str(l.get("lot_id")) for l in key_lots[:5]],
                "note": _note_for(key, key_lots),
            })
    out.sort(key=lambda d: -abs(d["delta"]))
    return out[:limit]


def _build_bridge(a: dict, b: dict, top_n: int = 8) -> list[dict[str, Any]]:
    amap, bmap = _driver_map(a, "by_underlying"), _driver_map(b, "by_underlying")
    deltas = {k: round(bmap.get(k, 0.0) - amap.get(k, 0.0), 2) for k in set(amap) | set(bmap)}
    ranked = sorted(deltas.items(), key=lambda kv: -abs(kv[1]))
    head, tail = ranked[:top_n], ranked[top_n:]
    bridge: list[dict[str, Any]] = [{"key": "start", "value": round(_f(a, "realized_pnl"), 2)}]
    for key, delta in head:
        if abs(delta) >= 0.005:
            bridge.append({"key": key, "value": delta})
    other = round(sum(v for _, v in tail), 2)
    if abs(other) >= 0.005:
        bridge.append({"key": "other", "value": other})
    bridge.append({"key": "end", "value": round(_f(b, "realized_pnl"), 2)})
    return bridge


# ---------------------------------------------------------------- facts

def facts_for_period(summary: dict) -> list[str]:
    """Standalone evidence sentences describing a single period."""
    s = summary
    out = [
        f"{s.get('period')}: realized P&L {usd(_f(s,'realized_pnl'))} across "
        f"{int(_f(s,'closed_lots'))} closed lots and {int(_f(s,'trade_count'))} trades.",
    ]
    if _f(s, "closed_lots"):
        out.append(
            f"Win rate {pct_plain(_f(s,'win_rate'))} ({int(_f(s,'wins'))}W / {int(_f(s,'losses'))}L); "
            f"average win {usd(_f(s,'avg_win'))}, average loss {usd(_f(s,'avg_loss'))}, "
            f"expectancy {usd(_f(s,'expectancy'))} per lot.")
        out.append(f"Average hold time {_f(s,'avg_hold_days'):.1f} days; profit factor {_f(s,'profit_factor'):.2f}.")
    out.append(
        f"Turnover {usd(_f(s,'turnover'))} ({usd(_f(s,'gross_bought'))} bought / {usd(_f(s,'gross_sold'))} sold); "
        f"fees {usd(_f(s,'fees'))}.")
    if s.get("concentration_top_symbol"):
        out.append(f"Largest allocation was {s['concentration_top_symbol']} at "
                   f"{pct_plain(_f(s,'concentration_top_share'))} of gross bought.")
    out.append(f"Options were {pct_plain(_f(s,'options_share'))} of trades; "
               f"dividends {usd(_f(s,'dividends'))}, interest {usd(_f(s,'interest'))}, "
               f"net deposits {usd(_f(s,'net_deposits'))}.")
    top = (s.get("by_underlying") or [])[:3]
    if top:
        out.append("Top P&L contributors: " + ", ".join(
            f"{d['key']} {usd(d['realized_pnl'])}" for d in top) + ".")
    return out


def _build_facts(a: dict, b: dict, drivers: Sequence[dict], lots: Sequence[dict]) -> list[str]:
    facts: list[str] = []
    pa, pb = a.get("period"), b.get("period")
    ra, rb = _f(a, "realized_pnl"), _f(b, "realized_pnl")
    delta = rb - ra
    ch = pct_change(ra, rb)
    verb = "fell" if delta < 0 else "rose"
    facts.append(
        f"Realized P&L {verb} from {usd(ra)} in {pa} to {usd(rb)} in {pb} "
        f"({usd(delta) if delta < 0 else '+' + usd(delta)}"
        + (f", {pct(ch)})." if ch is not None else ")."))

    denom = _denominator(a, b)
    und = [d for d in drivers if d["dimension"] == "underlying"]
    move = "the decline" if delta < 0 else "the improvement"
    for d in und[:3]:
        share = abs(d["contribution_pct"])
        n_lots = len(d["evidence_lot_ids"])
        ids = ", ".join(d["evidence_lot_ids"][:3])
        tail = f" ({ids})" if ids else ""
        # a driver moving against the overall swing offsets it rather than causing it
        aligned = (d["delta"] < 0) == (delta < 0)
        phrase = (f"contributed {usd(d['delta'])} ({pct_plain(share)} of {move})" if aligned
                  else f"offset {usd(abs(d['delta']))} of {move} ({pct_plain(share)})")
        facts.append(
            f"{d['key']} {phrase} across {n_lots} closed lot"
            f"{'s' if n_lots != 1 else ''} in {pb}{tail}.")

    at = [d for d in drivers if d["dimension"] == "asset_type"]
    if at:
        d = at[0]
        facts.append(f"By asset class, {d['key']} moved {usd(d['delta'])} "
                     f"({usd(d['a'])} in {pa} to {usd(d['b'])} in {pb}).")

    st = [d for d in drivers if d["dimension"] == "strategy" and d["key"] != "untagged"]
    if st:
        d = st[0]
        facts.append(f"The '{d['key']}' strategy tag moved {usd(d['delta'])} "
                     f"({usd(d['a'])} to {usd(d['b'])}) — {d['note']}.")

    ta, tb = _f(a, "trade_count"), _f(b, "trade_count")
    tch = pct_change(ta, tb)
    ha, hb = _f(a, "avg_hold_days"), _f(b, "avg_hold_days")
    facts.append(
        f"Trade count {'rose' if tb >= ta else 'fell'}"
        + (f" {pct(tch)}" if tch is not None else "")
        + f" ({int(ta)} → {int(tb)}); average hold time {'fell' if hb < ha else 'rose'} "
          f"from {ha:.1f} to {hb:.1f} days.")

    fa, fb = _f(a, "fees"), _f(b, "fees")
    tua, tub = _f(a, "turnover"), _f(b, "turnover")
    fch, tuch = pct_change(fa, fb), pct_change(tua, tub)
    facts.append(
        f"Fees {'rose' if fb >= fa else 'fell'} {usd(abs(fb - fa))}"
        + (f" ({pct(fch)})" if fch is not None else "")
        + f" with turnover {pct(tuch) if tuch is not None else 'n/a'} "
          f"({usd(tua)} → {usd(tub)}).")

    ca, cb = _f(a, "concentration_top_share"), _f(b, "concentration_top_share")
    facts.append(
        f"Top-symbol concentration {'rose' if cb >= ca else 'fell'} from {pct_plain(ca)} "
        f"({a.get('concentration_top_symbol') or 'n/a'}) to {pct_plain(cb)} "
        f"({b.get('concentration_top_symbol') or 'n/a'}) of gross bought.")

    wa, wb = _f(a, "win_rate"), _f(b, "win_rate")
    la, lb = _f(a, "avg_loss"), _f(b, "avg_loss")
    facts.append(
        f"Win rate {'fell' if wb < wa else 'rose'} from {pct_plain(wa)} to {pct_plain(wb)}; "
        f"average loss {'widened' if lb < la else 'narrowed'} from {usd(la)} to {usd(lb)}.")

    oa, ob = _f(a, "options_share"), _f(b, "options_share")
    facts.append(
        f"Options made up {pct_plain(ob)} of trades in {pb} versus {pct_plain(oa)} in {pa}; "
        f"expectancy per lot went from {usd(_f(a,'expectancy'))} to {usd(_f(b,'expectancy'))}.")

    lots_b = sorted([l for l in lots if period_of(str(l.get("close_ts") or "")) == pb],
                    key=lambda l: float(l.get("realized_pnl") or 0))
    if lots_b:
        worst = lots_b[0]
        best = lots_b[-1]
        if float(worst.get("realized_pnl") or 0) < 0:
            facts.append(
                f"The single largest loss in {pb} was {worst.get('symbol')} at "
                f"{usd(worst.get('realized_pnl'))} ({worst.get('lot_id')}), held "
                f"{float(worst.get('hold_days') or 0):.1f} days.")
        if float(best.get("realized_pnl") or 0) > 0:
            facts.append(
                f"The best lot in {pb} was {best.get('symbol')} at {usd(best.get('realized_pnl'))} "
                f"({best.get('lot_id')}).")

    da, db = _f(a, "dividends"), _f(b, "dividends")
    if da or db:
        facts.append(f"Dividends and interest totalled {usd(da + _f(a,'interest'))} in {pa} "
                     f"and {usd(db + _f(b,'interest'))} in {pb}.")

    nda, ndb = _f(a, "net_deposits"), _f(b, "net_deposits")
    if nda or ndb:
        facts.append(f"Net deposits were {usd(nda)} in {pa} and {usd(ndb)} in {pb}, so the "
                     f"P&L swing of {usd(delta)} is not explained by cash movement.")

    wk = [d for d in drivers if d["dimension"] == "weekday"]
    if wk:
        d = wk[0]
        facts.append(f"By entry weekday, positions opened on {d['key']} moved {usd(d['delta'])} "
                     f"({usd(d['a'])} → {usd(d['b'])}).")

    return facts[:15]


# ---------------------------------------------------------------- behaviour

def _behaviour_flags(a: dict, b: dict) -> list[str]:
    flags: list[str] = []
    ta, tb = _f(a, "trade_count"), _f(b, "trade_count")
    tch = pct_change(ta, tb)
    if tch is not None and abs(tch) >= 0.40:
        flags.append(f"trade_count {pct(tch)} ({int(ta)} → {int(tb)})")

    oa, ob = _f(a, "options_share"), _f(b, "options_share")
    if abs(ob - oa) >= 0.20:
        flags.append(f"options_share {oa:.2f}→{ob:.2f}")

    ha, hb = _f(a, "avg_hold_days"), _f(b, "avg_hold_days")
    if ha > 0 and hb <= ha / 2:
        flags.append(f"avg_hold_days halved {ha:.1f}→{hb:.1f}")
    elif ha > 0 and hb >= ha * 2:
        flags.append(f"avg_hold_days doubled {ha:.1f}→{hb:.1f}")

    cb = _f(b, "concentration_top_share")
    if cb > 0.40:
        flags.append(f"concentration {b.get('concentration_top_symbol')} {pct_plain(cb)} of gross bought")

    wa, wb = _f(a, "win_rate"), _f(b, "win_rate")
    if (wa - wb) >= 0.15:
        flags.append(f"win_rate −{(wa - wb) * 100:.0f}pts ({pct_plain(wa)}→{pct_plain(wb)})")

    la, lb = _f(a, "avg_loss"), _f(b, "avg_loss")
    if la < 0 and lb < 0 and abs(lb) >= abs(la) * 1.5:
        flags.append(f"avg_loss widened {usd(la)}→{usd(lb)}")

    fch = pct_change(_f(a, "fees"), _f(b, "fees"))
    if fch is not None and fch >= 0.40:
        flags.append(f"fees {pct(fch)}")
    tuch = pct_change(_f(a, "turnover"), _f(b, "turnover"))
    if tuch is not None and tuch >= 0.40:
        flags.append(f"turnover {pct(tuch)}")

    ea, eb = _f(a, "expectancy"), _f(b, "expectancy")
    if ea > 0 and eb < 0:
        flags.append(f"expectancy turned negative ({usd(ea)}→{usd(eb)})")
    return flags


# ---------------------------------------------------------------- public

def compare_periods(a: dict[str, Any], b: dict[str, Any],
                    lots: Sequence[dict[str, Any]] | None = None,
                    txns: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Full VarianceReport dict for period a (earlier) vs period b (later)."""
    lots = list(lots or [])
    topline = []
    for metric, label, fmt in TOPLINE_SPEC:
        av, bv = _f(a, metric), _f(b, metric)
        topline.append({
            "metric": metric,
            "label": label,
            "a": round(av, 4),
            "b": round(bv, 4),
            "delta": round(bv - av, 4),
            "pct": pct_change(av, bv),
            "format": fmt,
        })

    drivers = _build_drivers(a, b, lots)
    return {
        "a": a,
        "b": b,
        "topline": topline,
        "drivers": drivers,
        "bridge": _build_bridge(a, b),
        "facts": _build_facts(a, b, drivers, lots),
        "behaviour_flags": _behaviour_flags(a, b),
    }
