"""Analytics core: FIFO ledger, CSV parsers, the synthetic story, and the routes."""
from __future__ import annotations

from app.analytics.ledger import match_lots
from app.analytics.periods import all_period_summaries, list_periods, summarize_period
from app.analytics.positions import current_positions
from app.analytics.variance import compare_periods, facts_for_period
from app.ingest.generic_csv import detect_source, parse_generic_csv
from app.ingest.ibkr_flex_csv import parse_ibkr_flex_csv
from app.ingest.normalize import option_symbol, parse_money, to_iso_date
from app.ingest.robinhood_csv import parse_robinhood_activity_csv
from app.ingest.synthetic import generate_synthetic_book


def _t(**kw):
    base = dict(id="", source="test", account_id="A", ts="", symbol="", underlying="",
                asset_type="stock", side="none", open_close="", qty=0, price=0, fees=0,
                amount=0, description="", external_id="", strategy_tag="")
    base.update(kw)
    if not base["underlying"]:
        base["underlying"] = base["symbol"].split(" ")[0]
    return base


# --------------------------------------------------------------- normalize

def test_money_and_dates():
    assert parse_money("$1,234.56") == 1234.56
    assert parse_money("(1,234.56)") == -1234.56
    assert parse_money("($43.64)") == -43.64
    assert parse_money("") == 0.0
    assert parse_money("—") == 0.0
    assert to_iso_date("6/20/2025") == "2025-06-20"
    assert option_symbol("NVDA", "6/20/2026", "Call", "$140.00") == "NVDA 2026-06-20 C 140"
    assert option_symbol("SPY", "2026-06-20", "P", 140.5) == "SPY 2026-06-20 P 140.5"


# --------------------------------------------------------------- FIFO ledger

SIX = [
    # stock: buy 10 @ 100, buy 5 @ 120, sell 4 @ 110 (partial), sell 8 @ 130
    _t(id="t1", ts="2026-01-05T10:00:00", symbol="AAPL", side="buy", open_close="open",
       qty=10, price=100, amount=-1000),
    _t(id="t2", ts="2026-01-08T10:00:00", symbol="AAPL", side="buy", open_close="open",
       qty=5, price=120, amount=-600),
    _t(id="t3", ts="2026-01-15T10:00:00", symbol="AAPL", side="sell", open_close="close",
       qty=4, price=110, fees=0.40, amount=440),
    _t(id="t4", ts="2026-01-25T10:00:00", symbol="AAPL", side="sell", open_close="close",
       qty=8, price=130, fees=0.80, amount=1040),
    # option: buy 2 contracts @ 3.00 then expire worthless
    _t(id="t5", ts="2026-02-02T10:00:00", symbol="NVDA 2026-03-20 C 140", asset_type="option",
       side="buy", open_close="open", qty=2, price=3.0, fees=1.30, amount=-600,
       strategy_tag="earnings"),
    _t(id="t6", ts="2026-03-20T16:00:00", symbol="NVDA 2026-03-20 C 140", asset_type="option",
       side="none", open_close="close", qty=2, price=0, amount=0,
       description="Option Expiration for NVDA 3/20/2026 Call $140.00"),
]


def test_fifo_partial_fills_and_option_expiry():
    lots = match_lots(SIX)
    assert len(lots) == 4

    a, b, c, opt = lots
    # 4 shares out of the first 10-share lot @ 100 -> sold @ 110
    assert (a["qty"], a["cost"], a["proceeds"]) == (4.0, 400.0, 440.0)
    assert a["realized_pnl"] == 39.6 and a["hold_days"] == 10.0
    # next 6 from the SAME first lot (FIFO), sold @ 130
    assert (b["qty"], b["open_price"], b["cost"], b["proceeds"]) == (6.0, 100.0, 600.0, 780.0)
    assert b["realized_pnl"] == 179.4
    # then 2 from the second lot @ 120 (FIFO order respected)
    assert (c["qty"], c["open_price"], c["cost"], c["proceeds"]) == (2.0, 120.0, 240.0, 260.0)
    # option expiry closes at 0: full premium is the loss, 100x multiplier honoured
    assert opt["asset_type"] == "option" and opt["close_price"] == 0.0
    assert opt["cost"] == 600.0 and opt["proceeds"] == 0.0
    assert opt["realized_pnl"] == -601.3
    assert opt["strategy_tag"] == "earnings"
    assert opt["open_txn_ids"] == ["t5"] and opt["close_txn_ids"] == ["t6"]
    assert all(l["lot_id"].startswith("lot_") for l in lots)
    # 3 shares of the 120 lot are still open
    pos = current_positions(SIX)
    assert len(pos) == 1 and pos[0]["symbol"] == "AAPL" and pos[0]["qty"] == 3.0


def test_fifo_short_then_cover():
    txns = [
        _t(id="s1", ts="2026-04-01T10:00:00", symbol="TSLA", side="sell", open_close="open",
           qty=10, price=250, amount=2500),
        _t(id="s2", ts="2026-04-10T10:00:00", symbol="TSLA", side="buy", open_close="close",
           qty=10, price=230, fees=1.0, amount=-2300),
    ]
    lots = match_lots(txns)
    assert len(lots) == 1
    lot = lots[0]
    assert lot["proceeds"] == 2500.0 and lot["cost"] == 2300.0
    assert lot["realized_pnl"] == 199.0 and lot["hold_days"] == 9.0


def test_period_summary_basics():
    lots = match_lots(SIX)
    assert list_periods(SIX) == ["2026-01", "2026-02", "2026-03"]
    jan = summarize_period("2026-01", SIX, lots)
    assert jan["closed_lots"] == 3 and jan["trade_count"] == 4
    assert jan["realized_pnl"] == round(39.6 + 179.4 + 19.8, 2)
    assert jan["gross_bought"] == 1600.0 and jan["gross_sold"] == 1480.0
    assert jan["turnover"] == 3080.0
    assert jan["concentration_top_symbol"] == "AAPL"
    mar = summarize_period("2026-03", SIX, lots)
    # the expiration itself is not a "trade" but the lot lands in March
    assert mar["trade_count"] == 0 and mar["closed_lots"] == 1
    assert mar["options_share"] == 0.0
    assert len(facts_for_period(jan)) >= 4


# --------------------------------------------------------------- parsers

RH_CSV = '''﻿"Activity Date","Process Date","Settle Date","Instrument","Description","Trans Code","Quantity","Price","Amount"
"7/28/2026","7/28/2026","7/29/2026","NVDA","NVDA 8/21/2026 Call $150.00","STC","3","$5.10","$1,530.00"
"7/21/2026","7/21/2026","7/22/2026","NVDA","NVDA 8/21/2026 Call $150.00","BTO","3","$4.20","($1,260.00)"
"7/17/2026","7/17/2026","7/17/2026","LULU","Option Expiration for LULU 4/16/2026 Call $310.00","OEXP","2","","" 
"7/15/2026","7/15/2026","7/16/2026","AAPL","Apple Inc. - Common Stock","Sell","10","$232.50","$2,325.00"
"7/06/2026","7/06/2026","7/07/2026","AAPL","Apple Inc. - Common Stock","Buy","10","$228.00","($2,280.00)"
"7/03/2026","7/03/2026","7/03/2026","SPY","SPDR S&P 500 ETF","CDIV","—","—","$43.64"
"7/02/2026","7/02/2026","7/02/2026","","Robinhood Gold","GOLD","—","—","($5.00)"
"7/01/2026","7/01/2026","7/01/2026","","ACH Deposit","ACH","","","$2,000.00"
"7/01/2026","7/01/2026","7/01/2026","","Interest Payment","INT","—","—","$3.21"
"7/01/2026","7/01/2026","7/01/2026","","Withdrawal","ACH","","","($500.00)"

"Robinhood Securities LLC, member SIPC. The data provided is for informational purposes only."
'''


def test_robinhood_parser():
    txns = parse_robinhood_activity_csv(RH_CSV)
    assert len(txns) == 10, [t["description"] for t in txns]
    assert [t["ts"] for t in txns] == sorted(t["ts"] for t in txns)   # ascending
    by_code = {t["description"]: t for t in txns}

    bto = next(t for t in txns if t["side"] == "buy" and t["asset_type"] == "option")
    assert bto["symbol"] == "NVDA 2026-08-21 C 150"
    assert bto["underlying"] == "NVDA" and bto["open_close"] == "open"
    assert bto["qty"] == 3.0 and bto["price"] == 4.20 and bto["amount"] == -1260.0

    stc = next(t for t in txns if t["side"] == "sell" and t["asset_type"] == "option"
               and t["open_close"] == "close")
    assert stc["amount"] == 1530.0 and stc["symbol"] == bto["symbol"]

    oexp = next(t for t in txns if "Expiration" in t["description"])
    assert oexp["asset_type"] == "option" and oexp["side"] == "none"
    assert oexp["open_close"] == "close" and oexp["price"] == 0.0 and oexp["amount"] == 0.0
    assert oexp["symbol"] == "LULU 2026-04-16 C 310"

    div = next(t for t in txns if t["asset_type"] == "dividend")
    assert div["amount"] == 43.64 and div["underlying"] == "SPY"

    transfers = [t for t in txns if t["asset_type"] == "transfer"]
    assert sorted(t["amount"] for t in transfers) == [-500.0, 2000.0]   # parenthesised negative

    assert any(t["asset_type"] == "fee" and t["amount"] == -5.0 for t in txns)
    assert any(t["asset_type"] == "interest" for t in txns)
    assert all(t["id"].startswith("rh:") for t in txns)
    assert len({t["id"] for t in txns}) == len(txns)                    # dedupe-able ids

    # a real BTO/STC pair FIFO-matches into one lot
    lots = match_lots(txns)
    opt = [l for l in lots if l["asset_type"] == "option" and l["underlying"] == "NVDA"]
    assert len(opt) == 1 and opt[0]["realized_pnl"] == 270.0


IBKR_CSV = '''ClientAccountID,TradeDate,DateTime,Symbol,AssetClass,Buy/Sell,Quantity,TradePrice,Proceeds,IBCommission,NetCash,Open/CloseIndicator,Strike,Expiry,Put/Call,UnderlyingSymbol,Multiplier,TransactionID,Description
U1234567,20260701,"20260701;103102",AAPL,STK,BUY,10,190.5,-1905,-1.0,-1906.0,O,,,,AAPL,1,111111,APPLE INC
U1234567,20260710,"20260710;145533",AAPL,STK,SELL,-10,198.25,1982.5,-1.0,1981.5,C,,,,AAPL,1,111112,APPLE INC
U1234567,20260702,"20260702;093015",NVDA  260821C00150000,OPT,BUY,3,4.2,-1260,-1.95,-1261.95,O,150,20260821,C,NVDA,100,111113,NVDA 21AUG26 150 C
U1234567,20260728,"20260728;153000",NVDA  260821C00150000,OPT,SELL,-3,5.1,1530,-1.95,1528.05,C,150,20260821,C,NVDA,100,111114,NVDA 21AUG26 150 C
U1234567,20260705,"20260705;120000",BTC,CRYPTO,BUY,0.05,68420,-3421,-2.0,-3423.0,O,,,,BTC,1,111115,BITCOIN
'''


def test_ibkr_flex_parser():
    txns = parse_ibkr_flex_csv(IBKR_CSV)
    assert len(txns) == 5
    assert {t["account_id"] for t in txns} == {"U1234567"}
    assert all(t["source"] == "ibkr_flex" for t in txns)

    buy = next(t for t in txns if t["symbol"] == "AAPL" and t["side"] == "buy")
    assert buy["ts"] == "2026-07-01T10:31:02"
    assert buy["asset_type"] == "stock" and buy["open_close"] == "open"
    assert buy["qty"] == 10.0 and buy["amount"] == -1905.0 and buy["fees"] == 1.0

    sell = next(t for t in txns if t["symbol"] == "AAPL" and t["side"] == "sell")
    assert sell["qty"] == 10.0 and sell["amount"] > 0        # signed quantity -> sell

    opt = [t for t in txns if t["asset_type"] == "option"]
    assert len(opt) == 2
    assert {o["symbol"] for o in opt} == {"NVDA 2026-08-21 C 150"}
    assert {o["underlying"] for o in opt} == {"NVDA"}
    assert next(o for o in opt if o["side"] == "buy")["amount"] == -1260.0

    assert next(t for t in txns if t["asset_type"] == "crypto")["qty"] == 0.05

    lots = match_lots(txns)
    assert len(lots) == 2                                     # AAPL + NVDA round trips
    assert round(sum(l["realized_pnl"] for l in lots), 2) == round(775.5 + 266.1, 2)


def test_generic_parser_and_detection():
    assert detect_source(RH_CSV) == "robinhood"
    assert detect_source(IBKR_CSV) == "ibkr_flex"
    generic = ("ts,symbol,asset_type,side,qty,price,fees,amount\n"
               "2026-05-04T10:00:00,MSFT,stock,buy,5,415.00,0,-2075\n"
               "2026-05-12T10:00:00,MSFT,stock,sell,5,430.00,0.03,2150\n")
    assert detect_source(generic) == "generic"
    txns = parse_generic_csv(generic)
    assert len(txns) == 2 and txns[0]["underlying"] == "MSFT"
    lots = match_lots(txns)
    assert len(lots) == 1 and lots[0]["realized_pnl"] == 74.97


# --------------------------------------------------------------- the story

def test_synthetic_book_tells_the_july_story():
    txns = generate_synthetic_book()
    assert 250 <= len(txns) <= 400, len(txns)
    assert {t["account_id"] for t in txns} == {"DEMO-1"}
    assert {t["source"] for t in txns} == {"synthetic"}
    assert list_periods(txns) == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05",
                                 "2026-06", "2026-07", "2026-08"]
    # every trade lands on a weekday, every timestamp carries a time
    from datetime import datetime
    for t in txns:
        dt = datetime.fromisoformat(t["ts"])
        assert dt.weekday() < 5, t
        assert len(t["ts"]) == 19

    lots = match_lots(txns)
    sums = {s["period"]: s for s in all_period_summaries(txns, lots)}
    jun, jul, aug = sums["2026-06"], sums["2026-07"], sums["2026-08"]

    # Jan-May steady and positive
    for p in ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05"):
        assert sums[p]["realized_pnl"] > 0, p
        assert 6 <= sums[p]["avg_hold_days"] <= 12, p

    # June is the best month
    assert jun["realized_pnl"] == max(s["realized_pnl"] for s in sums.values())
    assert jun["realized_pnl"] > 1500

    # July is the bad month, and it is bad in every behavioural dimension
    assert jul["realized_pnl"] < jun["realized_pnl"] - 1500
    assert jul["trade_count"] > 1.6 * jun["trade_count"]
    assert jul["avg_hold_days"] < jun["avg_hold_days"] / 2
    assert jul["options_share"] > 0.65
    assert jul["concentration_top_symbol"] == "NVDA"
    assert jul["concentration_top_share"] > 0.45
    assert jul["fees"] > jun["fees"] * 1.4
    assert jul["win_rate"] < jun["win_rate"] - 0.15

    # August recovers with fewer trades and longer holds
    assert aug["realized_pnl"] > 0
    assert aug["trade_count"] < jul["trade_count"]
    assert aug["avg_hold_days"] > jul["avg_hold_days"] * 2

    # the demanded set pieces exist
    assert any("Expiration" in t["description"] for t in txns)
    assert any(t["asset_type"] == "crypto" and t["underlying"] == "BTC" for t in txns)
    assert {"earnings", "swing", "momentum"} <= {t["strategy_tag"] for t in txns}
    assert any(t["asset_type"] == "dividend" for t in txns)
    assert any(t["asset_type"] == "interest" for t in txns)
    assert any(t["asset_type"] == "transfer" and t["amount"] > 0 for t in txns)


def test_variance_blames_nvda_earnings():
    txns = generate_synthetic_book()
    lots = match_lots(txns)
    sums = {s["period"]: s for s in all_period_summaries(txns, lots)}
    rep = compare_periods(sums["2026-06"], sums["2026-07"], lots, txns)

    assert rep["a"]["period"] == "2026-06" and rep["b"]["period"] == "2026-07"
    assert {t["metric"] for t in rep["topline"]} >= {
        "realized_pnl", "fees", "trade_count", "win_rate", "avg_hold_days", "options_share",
        "turnover", "concentration_top_share", "expectancy"}

    und = [d for d in rep["drivers"] if d["dimension"] == "underlying"]
    assert und and und[0]["key"] == "NVDA"
    assert und[0]["contribution_pct"] > 0.5
    assert und[0]["evidence_lot_ids"]
    assert all(any(l["lot_id"] == lid and l["close_ts"][:7] == "2026-07" for l in lots)
               for lid in und[0]["evidence_lot_ids"])
    assert "lot" in und[0]["note"]

    strategies = {d["key"] for d in rep["drivers"] if d["dimension"] == "strategy"}
    assert "earnings" in strategies

    # bridge is a real waterfall that reconciles a -> b
    assert rep["bridge"][0] == {"key": "start", "value": sums["2026-06"]["realized_pnl"]}
    assert rep["bridge"][-1] == {"key": "end", "value": sums["2026-07"]["realized_pnl"]}
    middle = sum(step["value"] for step in rep["bridge"][1:-1])
    assert abs((rep["bridge"][0]["value"] + middle) - rep["bridge"][-1]["value"]) < 0.05

    assert 8 <= len(rep["facts"]) <= 15
    assert rep["behaviour_flags"]
    joined = " ".join(rep["behaviour_flags"])
    assert "trade_count" in joined and "options_share" in joined

    # facts must be numerically consistent with the summaries
    assert any("1,797" in f or "1,798" in f for f in rep["facts"])
    assert any("NVDA" in f and "%" in f for f in rep["facts"])


# --------------------------------------------------------------- routes

def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_routes_end_to_end(clean_db):
    client = _client()

    empty = client.get("/api/portfolio/overview")
    assert empty.status_code == 200 and empty.json()["has_data"] is False
    assert client.get("/api/periods").status_code == 400        # no data yet

    res = client.post("/api/ingest/demo")
    assert res.status_code == 200, res.text
    ing = res.json()
    assert ing["ok"] and ing["source"] == "synthetic" and ing["inserted"] > 250
    assert ing["account_ids"] == ["DEMO-1"]
    assert ing["date_range"]["start"].startswith("2026-01")
    assert ing["date_range"]["end"].startswith("2026-08")
    assert ing["asset_types"]["option"] > 0 and ing["asset_types"]["stock"] > 0

    # idempotent reload
    again = client.post("/api/ingest/demo").json()
    assert again["inserted"] == ing["inserted"]

    periods = client.get("/api/periods")
    assert periods.status_code == 200
    rows = periods.json()["periods"]
    assert [r["period"] for r in rows] == ["2026-01", "2026-02", "2026-03", "2026-04",
                                           "2026-05", "2026-06", "2026-07", "2026-08"]
    assert rows[-1]["ending_equity"] is not None        # snapshots came along for the ride

    one = client.get("/api/periods/2026-07")
    assert one.status_code == 200 and one.json()["concentration_top_symbol"] == "NVDA"
    assert client.get("/api/periods/2029-01").status_code == 404

    cmp_res = client.get("/api/periods/compare", params={"a": "2026-06", "b": "2026-07"})
    assert cmp_res.status_code == 200, cmp_res.text
    rep = cmp_res.json()
    assert set(rep) >= {"a", "b", "topline", "drivers", "bridge", "facts", "behaviour_flags"}
    assert len(rep["facts"]) >= 8
    assert rep["drivers"] and rep["behaviour_flags"]
    assert client.get("/api/periods/compare", params={"a": "2026-06", "b": "2029-01"}).status_code == 404

    ov = client.get("/api/portfolio/overview").json()
    assert ov["has_data"] is True
    assert set(ov) >= {"has_data", "sources", "transactions", "periods", "latest_period",
                       "equity_curve", "positions"}
    assert ov["latest_period"] == "2026-08"
    assert len(ov["equity_curve"]) == 8
    assert ov["equity_curve"][-1]["period"] == "2026-08"
    assert ov["sources"][0]["source"] == "synthetic"

    perf = client.get("/api/performance")
    assert perf.status_code == 200, perf.text
    pf = perf.json()
    assert set(pf) == {"monthly", "stats", "by_underlying", "positions"}
    assert len(pf["monthly"]) == 8
    assert set(pf["monthly"][0]) >= {"period", "realized_pnl", "cum_realized", "net_deposits",
                                     "cum_net_deposits", "fees", "trade_count", "win_rate"}
    assert pf["stats"]["best_period"] == "2026-06" and pf["stats"]["worst_period"] == "2026-07"
    assert pf["stats"]["max_drawdown"] <= 0 and pf["stats"]["months"] == 8
    assert pf["by_underlying"] and len(pf["by_underlying"]) <= 15

    txn_res = client.get("/api/transactions", params={"period": "2026-07", "underlying": "NVDA"})
    assert txn_res.status_code == 200
    txns = txn_res.json()["transactions"]
    assert txns and all(t["underlying"] == "NVDA" and t["ts"][:7] == "2026-07" for t in txns)

    lot_res = client.get("/api/lots", params={"period": "2026-07", "underlying": "NVDA", "limit": 5})
    assert lot_res.status_code == 200
    got = lot_res.json()["lots"]
    assert 0 < len(got) <= 5
    assert all(l["close_ts"][:7] == "2026-07" for l in got)


def test_csv_upload_route(clean_db):
    client = _client()
    res = client.post("/api/ingest/csv", files={"file": ("activity.csv", RH_CSV, "text/csv")})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["inserted"] == 10 and body["source"] == "robinhood_csv"
    assert body["asset_types"]["option"] == 3

    assert client.get("/api/periods/2026-07").status_code == 200
    assert client.post("/api/ingest/reset").json() == {"ok": True}
    assert client.get("/api/portfolio/overview").json()["has_data"] is False
