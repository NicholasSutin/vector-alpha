"""A fake `app.analytics.service` so the agent can be tested without the analytics module."""
from __future__ import annotations

import types


def _summary(period, pnl, fees, trades, hold, opt_share, conc_sym, conc, win_rate, avg_win, avg_loss):
    return {
        "period": period, "start": f"{period}-01", "end": f"{period}-28",
        "realized_pnl": pnl, "fees": fees, "dividends": 0.0, "interest": 0.0,
        "net_deposits": 0.0, "net_cash_flow": pnl - fees,
        "trade_count": trades, "closed_lots": trades // 2, "wins": 5, "losses": 5,
        "win_rate": win_rate, "avg_win": avg_win, "avg_loss": avg_loss,
        "expectancy": 12.0, "profit_factor": 1.2,
        "gross_bought": 50000.0, "gross_sold": 51000.0, "turnover": 101000.0,
        "avg_hold_days": hold, "options_share": opt_share,
        "concentration_top_symbol": conc_sym, "concentration_top_share": conc,
        "by_underlying": [{"key": "NVDA", "realized_pnl": pnl * 0.7, "trades": trades // 2,
                           "fees": fees / 2, "gross": 30000.0, "share": conc}],
        "by_asset_type": [{"key": "option", "realized_pnl": pnl * 0.8, "trades": trades // 2,
                           "fees": fees / 2, "gross": 20000.0, "share": opt_share}],
        "by_strategy": [{"key": "earnings", "realized_pnl": -1500.0 if period.endswith("07") else 200.0,
                         "trades": 6, "fees": 40.0, "gross": 9000.0, "share": 0.3}],
        "by_weekday": [], "largest_wins": [], "largest_losses": [],
        "ending_equity": None, "ending_cash": None,
    }


SUMMARY_A = _summary("2026-06", 3400.0, 90.0, 42, 9.0, 0.30, "NVDA", 0.28, 0.62, 420.0, -180.0)
SUMMARY_B = _summary("2026-07", 1260.0, 165.0, 78, 2.0, 0.68, "NVDA", 0.61, 0.41, 260.0, -390.0)


def _lot(i, sym="NVDA", pnl=-500.0):
    return {
        "lot_id": f"lot_{i}", "symbol": sym, "underlying": "NVDA", "asset_type": "option",
        "strategy_tag": "earnings", "open_ts": "2026-07-05T14:30:00Z", "close_ts": "2026-07-07T19:00:00Z",
        "qty": 2.0, "open_price": 5.2, "close_price": 2.6, "cost": 1040.0, "proceeds": 520.0,
        "fees": 2.6, "realized_pnl": pnl, "hold_days": 2.0,
        "open_txn_ids": [f"t_o{i}"], "close_txn_ids": [f"t_c{i}"],
    }


LOTS = [_lot(i, pnl=-500.0 - 50 * i) for i in range(1, 7)]

FACTS = [
    "Realized P&L fell $2,140 (-63%) from 2026-06 to 2026-07.",
    "NVDA options contributed 71% of the decline across 3 closed lots.",
    "Trade count rose from 42 to 78 (+86%).",
    "Average hold time collapsed from 9.0 days to 2.0 days.",
    "Fees rose from $90 to $165 (+83%).",
    "Options share of trades rose from 30% to 68%.",
    "Win rate fell from 62% to 41%.",
    "Average loss widened from $180 to $390.",
    "Concentration in NVDA rose from 28% to 61% of gross bought.",
    "Earnings-tagged trades lost $1,500 in 2026-07.",
]

DRIVERS = [
    {"dimension": "underlying", "key": "NVDA", "a": 1800.0, "b": -300.0, "delta": -2100.0,
     "contribution_pct": 0.71, "evidence_lot_ids": ["lot_1", "lot_2", "lot_3"],
     "note": "3 NVDA call lots opened the week before earnings"},
    {"dimension": "underlying", "key": "TSLA", "a": 600.0, "b": 200.0, "delta": -400.0,
     "contribution_pct": 0.14, "evidence_lot_ids": ["lot_4"], "note": "two swing lots cut early"},
    {"dimension": "asset_type", "key": "option", "a": 1200.0, "b": -500.0, "delta": -1700.0,
     "contribution_pct": 0.57, "evidence_lot_ids": ["lot_1", "lot_5"], "note": "options drove the loss"},
    {"dimension": "strategy", "key": "earnings", "a": 200.0, "b": -1500.0, "delta": -1700.0,
     "contribution_pct": 0.57, "evidence_lot_ids": ["lot_2", "lot_6"], "note": "earnings plays underwater"},
]

COMPARE = {
    "a": SUMMARY_A, "b": SUMMARY_B,
    "topline": [
        {"metric": "realized_pnl", "label": "Realized P&L", "a": 3400.0, "b": 1260.0,
         "delta": -2140.0, "pct": -0.63, "format": "usd"},
        {"metric": "fees", "label": "Fees", "a": 90.0, "b": 165.0, "delta": 75.0, "pct": 0.83, "format": "usd"},
        {"metric": "trade_count", "label": "Trades", "a": 42, "b": 78, "delta": 36, "pct": 0.86, "format": "int"},
        {"metric": "avg_hold_days", "label": "Avg hold", "a": 9.0, "b": 2.0, "delta": -7.0, "pct": -0.78, "format": "days"},
    ],
    "drivers": DRIVERS,
    "bridge": [{"key": "start", "value": 3400.0}, {"key": "NVDA", "value": -2100.0}, {"key": "end", "value": 1260.0}],
    "facts": FACTS,
    "behaviour_flags": ["trade count +86% (42→78)", "options_share 0.30→0.68", "avg_hold_days 9.0→2.0"],
}

POSITIONS = [
    {"symbol": "NVDA", "underlying": "NVDA", "asset_type": "stock", "qty": 9.0, "avg_cost": 118.0,
     "market_value": 1150.0, "unrealized_pnl": 88.0, "account_id": "DU123", "source": "synthetic"},
    {"symbol": "SPY", "underlying": "SPY", "asset_type": "stock", "qty": 4.0, "avg_cost": 540.0,
     "market_value": 2200.0, "unrealized_pnl": 40.0, "account_id": "DU123", "source": "synthetic"},
]


def make_fake_service() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        get_periods=lambda: ["2026-06", "2026-07"],
        get_summary=lambda period: SUMMARY_B if period == "2026-07" else SUMMARY_A,
        get_compare=lambda a, b: COMPARE,
        get_lots=lambda period=None, underlying=None, limit=200: LOTS[:limit],
        get_transactions=lambda **kw: [],
        get_positions=lambda: POSITIONS,
        latest_two_periods=lambda: ("2026-06", "2026-07"),
    )
