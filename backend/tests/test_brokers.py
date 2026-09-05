"""Broker connector tests — no live broker, everything through httpx.MockTransport."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.brokers import ibkr as ibkr_mod
from app.brokers.guard import PaperOnlyViolation, is_paper_account, require_paper_account
from app.brokers.ibkr import IBKRGateway
from app.brokers.ibkr_flex import FlexError, fetch_flex_statement
from app.db import get_conn, fetch_transactions, kv_set, now_iso

BASE = "https://localhost:5001/v1/api"


def make_gw(handler) -> IBKRGateway:
    return IBKRGateway(base_url=BASE, transport=httpx.MockTransport(handler))


def json_resp(payload, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


# --------------------------------------------------------------------------- guard
def test_guard_rejects_live_account():
    with pytest.raises(PaperOnlyViolation):
        require_paper_account("U1234567")
    with pytest.raises(PaperOnlyViolation):
        require_paper_account("")
    assert require_paper_account("DU123") == "DU123"
    assert require_paper_account("DF987") == "DF987"
    assert is_paper_account("DU1") and not is_paper_account("U1")


def test_guard_blocks_place_and_whatif():
    gw = make_gw(lambda r: json_resp([{"order_id": "1"}]))
    order = IBKRGateway.build_order(1, "BUY", 1)
    with pytest.raises(PaperOnlyViolation):
        gw.place("U1234567", order)
    with pytest.raises(PaperOnlyViolation):
        gw.whatif("U1234567", order)
    with pytest.raises(PaperOnlyViolation):
        gw.cancel("U1234567", "42")


# --------------------------------------------------------------------------- status / accounts
def test_status_and_accounts_parsing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/iserver/auth/status"):
            return json_resp({"authenticated": True, "connected": True, "competing": False, "message": ""})
        if request.url.path.endswith("/portfolio/accounts"):
            return json_resp([{"id": "DU1234567", "accountId": "DU1234567"}])
        if request.url.path.endswith("/iserver/accounts"):
            return json_resp({"accounts": ["DU1234567"], "selectedAccount": "DU1234567"})
        return json_resp({}, 404)

    gw = make_gw(handler)
    st = gw.status()
    assert st == {"authenticated": True, "connected": True, "competing": False, "message": ""}
    assert gw.accounts() == ["DU1234567"]
    assert gw.origin == "https://localhost:5001"


def test_status_defaults_when_gateway_returns_junk():
    gw = make_gw(lambda r: httpx.Response(200, text="not json"))
    st = gw.status()
    assert st["authenticated"] is False and st["connected"] is False


# --------------------------------------------------------------------------- conversions
def test_positions_to_models():
    raw = [
        {"conid": 265598, "contractDesc": "AAPL", "ticker": "AAPL", "position": 25,
         "avgCost": 180.5, "mktValue": 4700.0, "unrealizedPnl": 187.5, "assetClass": "STK",
         "acctId": "DU1234567"},
        {"conid": 999, "contractDesc": "NVDA JUN 20 '26 140 Call", "ticker": "NVDA", "position": -2,
         "avgCost": 812.0, "mktValue": -1500.0, "unrealizedPnl": 124.0, "assetClass": "OPT"},
    ]
    out = ibkr_mod.positions_to_models(raw, "DU1234567")
    assert out[0]["symbol"] == "AAPL" and out[0]["asset_type"] == "stock" and out[0]["qty"] == 25
    assert out[0]["market_value"] == 4700.0 and out[0]["account_id"] == "DU1234567"
    assert out[1]["asset_type"] == "option"
    assert out[1]["symbol"] == "NVDA 2026-06-20 C 140"
    assert out[1]["underlying"] == "NVDA" and out[1]["qty"] == -2
    # matches models.Position
    from app.models import Position

    assert Position(**out[1]).symbol == "NVDA 2026-06-20 C 140"


def test_trades_to_transactions_signs_and_option_symbol():
    trades = [
        {"execution_id": "E1", "symbol": "AAPL", "side": "B", "size": 10, "price": 190.0,
         "commission": 1.05, "trade_time": "20260705-14:31:02", "account": "DU1234567",
         "conid": 265598, "sec_type": "STK", "order_ref": "ref1"},
        {"execution_id": "E2", "symbol": "AAPL", "side": "S", "size": 10, "price": 195.0,
         "commission": 1.05, "trade_time_r": 1783000000000, "account": "DU1234567",
         "conid": 265598, "sec_type": "STK", "net_amount": 1950.0},
        {"execution_id": "E3", "symbol": "NVDA", "side": "B", "size": 2, "price": 8.12,
         "commission": 1.30, "trade_time": "20260705-15:00:00", "account": "DU1234567",
         "sec_type": "OPT", "contract_description_1": "NVDA JUN 20 '26 140 Call"},
    ]
    out = ibkr_mod.trades_to_transactions(trades, "DU1234567")
    buy, sell, opt = out
    assert buy["id"] == "ibkr:E1" and buy["source"] == "ibkr_live"
    assert buy["side"] == "buy" and buy["asset_type"] == "stock"
    assert buy["amount"] == pytest.approx(-(10 * 190.0) - 1.05)
    assert buy["ts"].startswith("2026-07-05T14:31:02")
    assert buy["fees"] == 1.05 and buy["external_id"] == "ref1"

    assert sell["side"] == "sell"
    assert sell["amount"] == pytest.approx(1950.0 - 1.05)     # prefers net_amount
    assert sell["ts"].startswith("2026-")

    assert opt["asset_type"] == "option" and opt["symbol"] == "NVDA 2026-06-20 C 140"
    assert opt["underlying"] == "NVDA"
    assert opt["amount"] == pytest.approx(-(2 * 8.12 * 100) - 1.30)   # 100x multiplier
    assert json.loads(opt["raw_json"])["execution_id"] == "E3"


def test_summary_equity_cash():
    eq, cash = ibkr_mod.summary_equity_cash(
        {"netliquidation": {"amount": 101234.5}, "totalcashvalue": {"amount": 5000.0}})
    assert eq == 101234.5 and cash == 5000.0


def test_save_snapshot_persists_positions():
    positions = ibkr_mod.positions_to_models(
        [{"ticker": "AAPL", "contractDesc": "AAPL", "position": 5, "avgCost": 100, "assetClass": "STK"}],
        "DU1234567")
    ibkr_mod.save_snapshot("DU1234567", positions, {"netliquidation": {"amount": 1000}, "totalcashvalue": {"amount": 400}})
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM snapshots").fetchone()
    assert row["equity"] == 1000 and row["cash"] == 400
    assert json.loads(row["positions_json"])[0]["symbol"] == "AAPL"


# --------------------------------------------------------------------------- market data / orders
def test_search_conid_and_snapshot():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path.endswith("/iserver/accounts"):
            return json_resp({"accounts": ["DU1"], "selectedAccount": "DU1"})
        if path.endswith("/iserver/secdef/search"):
            assert request.url.params["symbol"] == "AAPL"
            return json_resp([
                {"conid": 1, "symbol": "AAPLX", "sections": [{"secType": "STK"}]},
                {"conid": 265598, "symbol": "AAPL", "sections": [{"secType": "OPT"}, {"secType": "STK"}]},
            ])
        if path.endswith("/iserver/marketdata/snapshot"):
            if calls.count(path) == 1:
                return json_resp([{"conid": 265598}])          # priming call
            return json_resp([{"31": "C190.55", "84": "190.50", "86": "190.60"}])
        return json_resp({}, 404)

    gw = make_gw(handler)
    hit = gw.search_conid("AAPL")
    assert hit["conid"] == 265598
    snap = gw.snapshot(265598, prime_delay=0)
    assert snap["last"] == 190.55 and snap["bid"] == 190.50 and snap["ask"] == 190.60


def test_place_reply_loop():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path.endswith("/iserver/accounts"):
            return json_resp({"accounts": ["DU1234567"]})
        if path.endswith("/orders"):
            return json_resp([{"id": "reply-1", "message": ["Confirm order price?"], "isSuppressed": False}])
        if path.endswith("/reply/reply-1"):
            return json_resp([{"id": "reply-2", "message": ["Are you sure?"]}])
        if path.endswith("/reply/reply-2"):
            return json_resp([{"order_id": "77889", "order_status": "Submitted"}])
        return json_resp({}, 404)

    gw = make_gw(handler)
    order = IBKRGateway.build_order(265598, "BUY", 10, "LMT", 190.0, "DAY", "DU1234567")
    out = gw.place("DU1234567", order)
    assert out["ok"] and out["order_id"] == "77889" and out["order_status"] == "Submitted"
    assert out["messages"] == ["Confirm order price?", "Are you sure?"]
    assert any(p.endswith("/reply/reply-2") for p in seen)


def test_place_gives_up_after_too_many_replies():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/iserver/accounts"):
            return json_resp({"accounts": ["DU1"]})
        return json_resp([{"id": "loop", "message": ["again"]}])

    out = make_gw(handler).place("DU1234567", IBKRGateway.build_order(1, "BUY", 1))
    assert out["ok"] is False and "not confirmed" in out["messages"][-1]


# --------------------------------------------------------------------------- Flex
def test_flex_polling_in_progress_then_success():
    slept: list[float] = []
    state = {"gets": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("SendRequest"):
            assert request.url.params["t"] == "tok" and request.url.params["q"] == "qid"
            return httpx.Response(200, text=(
                "<FlexStatementResponse><Status>Success</Status><ReferenceCode>REF9</ReferenceCode>"
                "<Url>https://gdcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement</Url>"
                "</FlexStatementResponse>"))
        state["gets"] += 1
        assert request.url.params["q"] == "REF9"
        if state["gets"] == 1:
            return httpx.Response(200, text=(
                "<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode>"
                "<ErrorMessage>Statement generation in progress. Please try again shortly.</ErrorMessage>"
                "</FlexStatementResponse>"))
        return httpx.Response(200, text='ClientAccountID,Symbol,Buy/Sell\nDU1234567,AAPL,BUY\n')

    client = httpx.Client(transport=httpx.MockTransport(handler))
    text = fetch_flex_statement("tok", "qid", client=client, sleep=slept.append)
    assert text.startswith("ClientAccountID")
    assert slept == [5.0]


def test_flex_timeout_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("SendRequest"):
            return httpx.Response(200, text="<Status>Success</Status><ReferenceCode>R</ReferenceCode>")
        return httpx.Response(200, text="<ErrorCode>1019</ErrorCode>Statement generation in progress")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(FlexError):
        fetch_flex_statement("t", "q", client=client, sleep=lambda s: None, max_wait=15)


def test_flex_send_request_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<Status>Fail</Status><ErrorCode>1012</ErrorCode>"
                                        "<ErrorMessage>Token invalid</ErrorMessage>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(FlexError, match="1012"):
        fetch_flex_statement("bad", "q", client=client, sleep=lambda s: None)


# --------------------------------------------------------------------------- routes
@pytest.fixture()
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


def _patch_gateway(monkeypatch, handler) -> None:
    import app.routes.brokers as routes

    monkeypatch.setattr(routes, "gateway", lambda: make_gw(handler))


def test_route_status_when_gateway_unreachable(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _patch_gateway(monkeypatch, handler)
    r = client.get("/api/brokers/ibkr/status")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False
    assert body["authenticated"] is False
    assert body["login_url"] == "https://localhost:5001"
    assert "ibkr-gateway.sh" in body["message"]


def test_route_status_reachable_selects_paper_account(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/iserver/auth/status"):
            return json_resp({"authenticated": True, "connected": True})
        if request.url.path.endswith("/portfolio/accounts"):
            return json_resp([{"accountId": "DU1234567"}])
        return json_resp({}, 404)

    _patch_gateway(monkeypatch, handler)
    body = client.get("/api/brokers/ibkr/status").json()
    assert body["reachable"] and body["accounts"] == ["DU1234567"]
    assert body["selected_account"] == "DU1234567" and body["paper"] is True


def test_route_select_account_rejects_live(client):
    r = client.post("/api/brokers/ibkr/select_account", json={"account_id": "U1234567"})
    assert r.status_code == 403
    assert "paper" in r.json()["detail"].lower()


def test_route_preview_requires_paper_account(client, monkeypatch):
    kv_set("ibkr_account_id", "U1234567")
    _patch_gateway(monkeypatch, lambda r: json_resp({}))
    r = client.post("/api/brokers/ibkr/orders/preview",
                    json={"symbol": "AAPL", "side": "BUY", "qty": 1, "order_type": "MKT"})
    assert r.status_code == 403


def test_route_preview_then_place(client, monkeypatch):
    kv_set("ibkr_account_id", "DU1234567")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/iserver/accounts"):
            return json_resp({"accounts": ["DU1234567"]})
        if path.endswith("/iserver/secdef/search"):
            return json_resp([{"conid": 265598, "symbol": "AAPL", "sections": [{"secType": "STK"}]}])
        if path.endswith("/orders/whatif"):
            return json_resp({"amount": {"amount": "$1,905.00", "commission": "1.05"},
                              "equity": {"current": "100000", "after": "99998"},
                              "warn": "Price outside typical range"})
        if path.endswith("/iserver/account/DU1234567/orders"):
            return json_resp([{"order_id": "55555", "order_status": "Submitted"}])
        return json_resp({}, 404)

    _patch_gateway(monkeypatch, handler)
    prev = client.post("/api/brokers/ibkr/orders/preview",
                       json={"symbol": "AAPL", "side": "BUY", "qty": 10, "order_type": "MKT",
                             "rationale": "test"}).json()
    assert prev["conid"] == 265598
    assert prev["order"]["preview"]["commission"] == 1.05
    assert prev["order"]["preview"]["equity_with_loan_after"] == 99998.0
    assert prev["order"]["status"] == "previewed"

    placed = client.post("/api/brokers/ibkr/orders/place", json={"order_id": prev["order_id"]}).json()
    assert placed["status"] == "submitted" and placed["broker_order_id"] == "55555"

    # cannot place twice
    again = client.post("/api/brokers/ibkr/orders/place", json={"order_id": prev["order_id"]})
    assert again.status_code == 409


def test_route_place_requires_previous_preview(client):
    r = client.post("/api/brokers/ibkr/orders/place", json={"order_id": "nope"})
    assert r.status_code == 404


def test_orders_refresh_records_fill_transaction(client, monkeypatch):
    kv_set("ibkr_account_id", "DU1234567")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO orders (id, created_at, account_id, symbol, conid, side, qty, order_type, tif,"
            " status, broker_order_id, preview_json, rationale) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ord_x", now_iso(), "DU1234567", "AAPL", 265598, "BUY", 10, "MKT", "DAY",
             "submitted", "55555", json.dumps({"preview": {"amount": {"commission": "1.05"}}}),
             "buy the dip because reasons"),
        )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/iserver/accounts"):
            return json_resp({"accounts": ["DU1234567"]})
        if "/order/status/55555" in path:
            return json_resp({"order_status": "Filled", "avg_price": "190.00", "cum_fill": "10",
                              "last_execution_time": "20260705-14:31:02"})
        if path.endswith("/iserver/marketdata/snapshot"):
            return json_resp([{"31": "192.00"}])
        return json_resp({}, 404)

    _patch_gateway(monkeypatch, handler)
    body = client.post("/api/brokers/ibkr/orders/refresh").json()
    order = body["orders"][0]
    assert order["status"] == "filled"
    assert order["fill"]["price"] == 190.0 and order["fill"]["qty"] == 10
    assert order["fill"]["time"].startswith("2026-07-05T14:31:02")
    assert order["mark"] == 192.0
    assert order["pnl_since_fill"] == pytest.approx((192.0 - 190.0) * 10)
    assert order["preview"]["commission"] == 1.05

    txns = fetch_transactions("id=?", ("ibkr:order:55555",))
    assert len(txns) == 1
    assert txns[0]["source"] == "ibkr_live" and txns[0]["side"] == "buy"
    assert txns[0]["amount"] == pytest.approx(-1900.0)
    assert txns[0]["description"].startswith("agent paper trade: buy the dip")

    # idempotent: a second refresh does not duplicate the transaction
    client.post("/api/brokers/ibkr/orders/refresh")
    assert len(fetch_transactions("id=?", ("ibkr:order:55555",))) == 1


def test_route_orders_list_survives_dead_gateway(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    _patch_gateway(monkeypatch, handler)
    r = client.get("/api/brokers/ibkr/orders")
    assert r.status_code == 200 and r.json()["orders"] == []


def test_route_flex_missing_credentials(client):
    r = client.post("/api/brokers/ibkr/flex", json={"token": "", "query_id": ""})
    assert r.status_code == 400


def test_robinhood_status_route(client):
    r = client.get("/api/brokers/robinhood/status")
    assert r.status_code == 200 and r.json()["logged_in"] is False


def test_robinhood_login_challenge_when_input_prompted(monkeypatch):
    from app.brokers import robinhood as rh

    from robin_stocks import robinhood as rs

    def fake_login(*a, **kw):
        input("Enter the SMS code: ")   # robin_stocks does exactly this

    monkeypatch.setattr(rs, "login", fake_login)
    out = rh.login("u", "p")
    assert out["status"] == "challenge"
    assert "mfa_code" in out["message"] or "Robinhood app" in out["message"]


def test_robinhood_login_success_and_sync_conversion(monkeypatch):
    from app.brokers import robinhood as rh
    from robin_stocks import robinhood as rs

    monkeypatch.setitem(rh._state, "logged_in", False)   # restored after the test
    monkeypatch.setitem(rh._state, "username", "")
    monkeypatch.setattr(rs, "login", lambda *a, **kw: {"access_token": "tok", "detail": "logged in"})
    assert rh.login("u", "p")["status"] == "ok"

    monkeypatch.setattr(rs, "get_all_stock_orders", lambda: [
        {"id": "o1", "state": "filled", "side": "buy", "cumulative_quantity": "5",
         "average_price": "100.0", "fees": "0.02", "instrument": "https://api.robinhood.com/instruments/i1/",
         "last_transaction_at": "2026-07-05T14:31:02.123456Z"},
        {"id": "o2", "state": "cancelled", "side": "buy", "cumulative_quantity": "5",
         "average_price": "100.0", "instrument": "https://api.robinhood.com/instruments/i1/"},
    ])
    monkeypatch.setattr(rs, "get_symbol_by_url", lambda url: "AAPL")
    monkeypatch.setattr(rs, "get_all_option_orders", lambda: [
        {"id": "op1", "state": "filled", "processed_premium": "812.00", "processed_quantity": "2",
         "chain_symbol": "NVDA", "updated_at": "2026-07-06T15:00:00Z",
         "legs": [{"side": "buy", "position_effect": "open",
                   "option": "https://api.robinhood.com/options/instruments/opt-uuid/",
                   "executions": [{"quantity": "2", "price": "4.06"}]}]},
    ])
    monkeypatch.setattr(rs, "get_option_instrument_data_by_id", lambda oid: {
        "chain_symbol": "NVDA", "expiration_date": "2026-06-20", "strike_price": "140.0000", "type": "call"})
    monkeypatch.setattr(rs, "get_all_crypto_orders", lambda: [])
    monkeypatch.setattr(rs, "get_dividends", lambda: [
        {"id": "d1", "amount": "12.50", "paid_at": "2026-07-10T00:00:00Z", "state": "paid",
         "instrument": "https://api.robinhood.com/instruments/i1/", "position": "5", "rate": "2.5"}])
    monkeypatch.setattr(rs, "get_bank_transfers", lambda: [
        {"id": "b1", "amount": "500.00", "direction": "withdraw", "state": "completed",
         "created_at": "2026-07-02T00:00:00Z"}])
    monkeypatch.setattr(rs, "get_interest_payments", lambda: [])

    res = rh.sync("rh-1")
    assert res["inserted"] == 4 and res["source"] == "robinhood_live"
    rows = {t["id"]: t for t in fetch_transactions()}
    assert rows["rhl:o1"]["amount"] == pytest.approx(-500.02) and rows["rhl:o1"]["symbol"] == "AAPL"
    assert "rhl:o2" not in rows                       # cancelled orders skipped
    leg = rows["rhl:op1:L0"]
    assert leg["symbol"] == "NVDA 2026-06-20 C 140" and leg["asset_type"] == "option"
    assert leg["open_close"] == "open" and leg["amount"] == pytest.approx(-812.0)
    assert rows["rhl:d1"]["asset_type"] == "dividend" and rows["rhl:d1"]["amount"] == 12.5
    assert rows["rhl:b1"]["asset_type"] == "transfer" and rows["rhl:b1"]["amount"] == -500.0
    assert res["date_range"]["start"].startswith("2026-07-02")


def test_demo_connection_preview_place_fill(client, monkeypatch):
    """IBKR_DEMO=1: the desk simulates a paper session end-to-end without any gateway."""
    from app.config import settings as _settings
    from app.db import fetch_transactions

    from app.db import kv_set
    object.__setattr__(_settings, "ibkr_demo", "1")
    kv_set("ibkr_account_id", "")   # no real paper account selected -> the pseudo DU-DEMO account
    st = client.get("/api/brokers/ibkr/status").json()
    assert st["demo"] is True and st["authenticated"] is True and st["selected_account"] == "DU-DEMO"

    pv = client.post("/api/brokers/ibkr/orders/preview",
                     json={"symbol": "NVDA", "side": "SELL", "qty": 2, "order_type": "MKT", "rationale": "trim"})
    assert pv.status_code == 200, pv.text
    body = pv.json()
    assert body["account_id"] == "DU-DEMO" and body["preview"]["demo"] is True

    pl = client.post("/api/brokers/ibkr/orders/place", json={"order_id": body["order_id"]})
    assert pl.status_code == 200, pl.text
    assert pl.json()["status"] == "filled"
    order = pl.json()["order"]
    assert order["fill"]["price"] > 0 and order["mark"] and order["pnl_since_fill"] is not None

    listed = client.get("/api/brokers/ibkr/orders").json()["orders"]
    assert listed and listed[0]["status"] == "filled"
    txns = fetch_transactions("id LIKE ?", ("ibkr:order:demo-%",))
    assert len(txns) == 1 and txns[0]["side"] == "sell"
