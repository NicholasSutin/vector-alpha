"""Broker endpoints (docs/API.md -> Brokers).

IBKR is paper-only: every order-mutating handler goes through
`app.brokers.guard.require_paper_account`, and `PaperOnlyViolation` is mapped to
HTTP 403. `/brokers/ibkr/status` never raises — an unreachable gateway is a
200 with `reachable: false` plus the login URL to open in a browser.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.brokers import ibkr as ibkr_mod
from app.brokers import robinhood as rh_mod
from app.brokers.guard import PaperOnlyViolation, is_paper_account, require_paper_account
from app.brokers.ibkr import IBKRGateway, IBKRError
from app.config import settings
from app.db import get_conn, kv_get, kv_set, new_id, now_iso, rows_to_dicts

log = logging.getLogger("vector-alpha.routes.brokers")
router = APIRouter(prefix="/brokers", tags=["brokers"])

ACCOUNT_KEY = "ibkr_account_id"


def gateway() -> IBKRGateway:
    """Factory so tests can monkeypatch the transport/base url."""
    return IBKRGateway()


def selected_account() -> str:
    return (kv_get(ACCOUNT_KEY) or settings.ibkr_account_id or "").strip()


def _paper_error(e: PaperOnlyViolation) -> HTTPException:
    return HTTPException(status_code=403, detail=str(e))


# --------------------------------------------------------------------------- bodies
class SelectAccountBody(BaseModel):
    account_id: str


class FlexBody(BaseModel):
    token: str = ""
    query_id: str = ""


class QuoteBody(BaseModel):
    symbol: str


class PreviewBody(BaseModel):
    symbol: str
    side: str
    qty: float
    order_type: str = "MKT"
    limit_price: Optional[float] = None
    tif: str = "DAY"
    run_id: Optional[str] = None
    rationale: str = ""


class PlaceBody(BaseModel):
    order_id: str


class RHLoginBody(BaseModel):
    username: str
    password: str
    mfa_code: Optional[str] = None


# --------------------------------------------------------------------------- IBKR
@router.get("/ibkr/status")
def ibkr_status() -> dict[str, Any]:
    """Never throws. Unreachable gateway -> reachable:false + login_url."""
    gw = gateway()
    out: dict[str, Any] = {
        "configured": bool(settings.ibkr_gateway_url),
        "gateway_url": gw.base_url,
        "reachable": False,
        "authenticated": False,
        "connected": False,
        "competing": False,
        "accounts": [],
        "selected_account": selected_account() or None,
        "paper": is_paper_account(selected_account()),
        "login_url": gw.origin,
        "paper_only": settings.ibkr_paper_only,
        "message": "",
    }
    try:
        st = gw.status()
        out.update({
            "reachable": True,
            "authenticated": bool(st.get("authenticated")),
            "connected": bool(st.get("connected")),
            "competing": bool(st.get("competing")),
            "message": str(st.get("message") or ""),
        })
        if not out["authenticated"]:
            out["message"] = (
                f"Gateway is up but not logged in — open {gw.origin} in a browser and log in "
                f"with your PAPER username (account id DU…)."
            )
            return out
    except Exception as e:  # noqa: BLE001 - status must never fail
        out["message"] = (
            f"IBKR gateway not reachable ({e}). Start it with scripts/ibkr-gateway.sh, "
            f"then log in with your PAPER username at {gw.origin}."
        )
        return out

    try:
        accounts = gw.accounts()
        out["accounts"] = accounts
        if not out["selected_account"]:
            paper = [a for a in accounts if is_paper_account(a)]
            if paper:
                out["selected_account"] = paper[0]
                kv_set(ACCOUNT_KEY, paper[0])
        out["paper"] = is_paper_account(out["selected_account"])
    except Exception as e:  # noqa: BLE001
        out["message"] = out["message"] or f"Authenticated session not ready: {e}"
    return out


@router.post("/ibkr/select_account")
def ibkr_select_account(body: SelectAccountBody) -> dict[str, Any]:
    acct = (body.account_id or "").strip()
    if not acct:
        raise HTTPException(status_code=400, detail="account_id is required")
    if settings.ibkr_paper_only and not is_paper_account(acct):
        raise HTTPException(
            status_code=403,
            detail=(f"'{acct}' is not a paper account. Vector Alpha only connects to IBKR "
                    "paper accounts (ids starting with DU or DF)."),
        )
    kv_set(ACCOUNT_KEY, acct)
    return ibkr_status()


@router.post("/ibkr/sync")
def ibkr_sync(days: int = 7) -> dict[str, Any]:
    """Positions + summary -> snapshots; recent trades -> transactions (ibkr_live)."""
    from app.brokers.ibkr_flex import build_ingest_result
    from app.db import insert_transactions

    acct = selected_account()
    if not acct:
        raise HTTPException(status_code=400, detail="No IBKR account selected. POST /brokers/ibkr/select_account first.")
    gw = gateway()
    warnings: list[str] = []
    try:
        raw_positions = gw.positions(acct)
    except IBKRError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    positions = ibkr_mod.positions_to_models(raw_positions, acct)

    summary: dict[str, Any] = {}
    try:
        summary = gw.summary(acct)
    except IBKRError as e:
        warnings.append(f"summary unavailable: {e}")

    try:
        ibkr_mod.save_snapshot(acct, positions, summary)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"snapshot not stored: {e}")

    txns: list[dict[str, Any]] = []
    try:
        txns = ibkr_mod.trades_to_transactions(gw.trades(days=days), acct)
    except IBKRError as e:
        warnings.append(f"trades unavailable: {e}")

    inserted, skipped = insert_transactions(txns)
    result = build_ingest_result("ibkr_live", txns, inserted, skipped, warnings)
    result["positions"] = positions
    equity, cash = ibkr_mod.summary_equity_cash(summary)
    result["equity"] = equity
    result["cash"] = cash
    return result


@router.post("/ibkr/flex")
def ibkr_flex(body: FlexBody) -> dict[str, Any]:
    from app.brokers.ibkr_flex import FlexError, ingest_flex

    token = (body.token or settings.ibkr_flex_token or "").strip()
    query_id = (body.query_id or settings.ibkr_flex_query_id or "").strip()
    if not token or not query_id:
        raise HTTPException(status_code=400, detail="Flex token and query_id are required (or set IBKR_FLEX_* in .env).")
    try:
        return ingest_flex(token, query_id, account_id=selected_account() or None)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except FlexError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/ibkr/quote")
def ibkr_quote(body: QuoteBody) -> dict[str, Any]:
    gw = gateway()
    sym = (body.symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    try:
        hit = gw.search_conid(sym)
        if not hit or not hit.get("conid"):
            raise HTTPException(status_code=404, detail=f"No IBKR contract found for '{sym}'.")
        conid = hit["conid"]
        snap = gw.snapshot(conid)
    except IBKRError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"symbol": sym, "conid": conid, "last": snap.get("last"),
            "bid": snap.get("bid"), "ask": snap.get("ask")}


# --------------------------------------------------------------------------- orders
ORDER_COLS = ["id", "created_at", "run_id", "account_id", "symbol", "conid", "side", "qty",
              "order_type", "limit_price", "tif", "status", "broker_order_id",
              "preview_json", "result_json", "rationale"]


def _order_row(order_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    return dict(r) if r else None


NON_TERMINAL = {"proposed", "previewed", "submitted", "partially_filled"}
STATUS_MAP = {
    "filled": "filled",
    "cancelled": "cancelled", "canceled": "cancelled", "pendingcancel": "cancelled",
    "inactive": "rejected", "rejected": "rejected",
    "submitted": "submitted", "presubmitted": "submitted", "pendingsubmit": "submitted",
}


def _find(d: Any, *needles: str) -> Any:
    """Depth-first search for the first key containing any needle (case-insensitive)."""
    if isinstance(d, dict):
        for k, v in d.items():
            kl = str(k).lower()
            if any(n in kl for n in needles) and not isinstance(v, (dict, list)):
                return v
        for v in d.values():
            if isinstance(v, (dict, list)):
                got = _find(v, *needles)
                if got is not None:
                    return got
    elif isinstance(d, list):
        for v in d:
            got = _find(v, *needles)
            if got is not None:
                return got
    return None


def _num_or_none(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def parse_preview(preview: Any) -> dict[str, Any]:
    """Best-effort normalization of the gateway whatif payload (key names vary)."""
    p = preview if isinstance(preview, dict) else {}
    warnings: list[str] = []
    for key in ("warn", "warning", "error", "message", "text"):
        v = p.get(key)
        if isinstance(v, list):
            warnings.extend(str(x) for x in v if x)
        elif v:
            warnings.append(str(v))
    equity = p.get("equity") if isinstance(p.get("equity"), dict) else {}
    before = _num_or_none(equity.get("current") if equity else None)
    after = _num_or_none(equity.get("after") if equity else None)
    if before is None:
        before = _num_or_none(_find(p, "equity_with_loan_before", "equitywithloanbefore"))
    if after is None:
        after = _num_or_none(_find(p, "equity_with_loan_after", "equitywithloanafter"))
    amount = p.get("amount")
    if isinstance(amount, dict):
        amount_str = amount.get("amount")
        commission = _num_or_none(amount.get("commission"))
    else:
        amount_str = amount
        commission = None
    if commission is None:
        commission = _num_or_none(_find(p, "commission"))
    return {
        "commission": commission,
        "equity_with_loan_before": before,
        "equity_with_loan_after": after,
        "amount": str(amount_str) if amount_str not in (None, "") else None,
        "warnings": warnings,
        "raw": p or None,
    }


def _order_out(row: dict[str, Any]) -> dict[str, Any]:
    """Row -> the enriched Order shape from docs/API.md."""
    try:
        stored_preview = json.loads(row.get("preview_json") or "null") or {}
    except (TypeError, ValueError):
        stored_preview = {}
    try:
        result = json.loads(row.get("result_json") or "null") or {}
    except (TypeError, ValueError):
        result = {}

    preview = parse_preview(stored_preview.get("preview")) if stored_preview else None
    fill = result.get("fill") if isinstance(result.get("fill"), dict) else None
    mark = result.get("mark")
    qty = float(row.get("qty") or 0)
    pnl = None
    if fill and fill.get("price") and mark:
        sign = 1.0 if str(row.get("side", "")).upper() == "BUY" else -1.0
        fq = float(fill.get("qty") or qty or 0)
        pnl = (float(mark) - float(fill["price"])) * fq * sign
    messages = [str(m) for m in (result.get("messages") or [])]
    if result.get("error"):
        messages.append(str(result["error"]))
    return {
        "id": row.get("id"),
        "created_at": row.get("created_at"),
        "run_id": row.get("run_id"),
        "account_id": row.get("account_id"),
        "symbol": row.get("symbol"),
        "conid": row.get("conid"),
        "side": row.get("side"),
        "qty": qty,
        "order_type": row.get("order_type"),
        "limit_price": row.get("limit_price"),
        "tif": row.get("tif"),
        "status": row.get("status"),
        "broker_order_id": row.get("broker_order_id"),
        "rationale": row.get("rationale") or "",
        "preview": preview,
        "fill": fill,
        "mark": mark,
        "pnl_since_fill": pnl,
        "messages": messages,
    }


def _record_fill_transaction(row: dict[str, Any], fill: dict[str, Any]) -> None:
    """Insert the fill as an ibkr_live transaction so the next agent run sees it."""
    from app.db import insert_transactions

    bid = str(row.get("broker_order_id") or "")
    price = float(fill.get("price") or 0)
    qty = float(fill.get("qty") or row.get("qty") or 0)
    if not bid or qty <= 0:
        return
    side = str(row.get("side") or "").lower()
    gross = qty * price
    amount = -gross if side == "buy" else gross
    sym = str(row.get("symbol") or "")
    insert_transactions([{
        "id": f"ibkr:order:{bid}",
        "source": "ibkr_live",
        "account_id": str(row.get("account_id") or ""),
        "ts": fill.get("time") or now_iso(),
        "symbol": sym, "underlying": sym, "asset_type": "stock",
        "side": side, "open_close": "", "qty": qty, "price": price, "fees": 0.0,
        "amount": amount,
        "description": "agent paper trade: " + str(row.get("rationale") or "")[:80],
        "external_id": bid, "strategy_tag": "agent",
        "raw_json": json.dumps({"order": row.get("id"), "fill": fill}, default=str),
    }])


def _refresh_orders(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best-effort refresh of non-terminal orders from the gateway. Never raises."""
    targets = [r for r in rows if r.get("broker_order_id") and str(r.get("status")) in NON_TERMINAL]
    if not targets:
        return rows
    try:
        gw = gateway()
    except Exception as e:  # noqa: BLE001
        log.debug("gateway unavailable for refresh: %s", e)
        return rows

    marks: dict[str, Optional[float]] = {}
    for row in targets:
        try:
            st = gw.order_status(str(row["broker_order_id"]))
        except Exception as e:  # noqa: BLE001 - unreachable gateway -> stored rows
            log.debug("order status refresh failed: %s", e)
            continue
        raw_status = str(st.get("order_status") or st.get("status") or "").lower().replace(" ", "")
        cum = _num_or_none(st.get("cum_fill") or st.get("filled_quantity") or st.get("filledQuantity")) or 0.0
        price = _num_or_none(st.get("avg_price") or st.get("average_price") or st.get("avgPrice"))
        new_status = STATUS_MAP.get(raw_status, row.get("status"))
        if new_status != "filled" and cum > 0 and new_status in {"submitted", "partially_filled"}:
            new_status = "partially_filled"
        fill = None
        if cum > 0 or new_status == "filled":
            fill = {
                "price": price,
                "qty": cum or float(row.get("qty") or 0),
                "time": ibkr_mod.ib_time_to_iso(
                    st.get("last_execution_time_r") or st.get("last_execution_time")),
            }

        mark = None
        conid = row.get("conid")
        if conid:
            key = str(conid)
            if key not in marks:
                try:
                    marks[key] = gw.snapshot(conid, fields="31", prime=True, prime_delay=0.3).get("last")
                except Exception:  # noqa: BLE001
                    marks[key] = None
            mark = marks[key]

        try:
            result = json.loads(row.get("result_json") or "null") or {}
        except (TypeError, ValueError):
            result = {}
        result.update({k: v for k, v in (("fill", fill), ("mark", mark), ("order_status", raw_status)) if v is not None})
        was = row.get("status")
        row["status"] = new_status or was
        row["result_json"] = json.dumps(result, default=str)
        try:
            with get_conn() as conn:
                conn.execute("UPDATE orders SET status=?, result_json=? WHERE id=?",
                             (row["status"], row["result_json"], row["id"]))
        except Exception as e:  # noqa: BLE001
            log.warning("could not persist order refresh: %s", e)
        if row["status"] == "filled" and was != "filled" and fill:
            try:
                _record_fill_transaction(row, fill)
            except Exception as e:  # noqa: BLE001
                log.warning("could not record fill transaction: %s", e)
    return rows


@router.post("/ibkr/orders/preview")
def ibkr_order_preview(body: PreviewBody) -> dict[str, Any]:
    acct = selected_account()
    try:
        acct = require_paper_account(acct)
    except PaperOnlyViolation as e:
        raise _paper_error(e) from e

    side = (body.side or "").upper()
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")
    order_type = (body.order_type or "MKT").upper()
    if order_type not in {"MKT", "LMT"}:
        raise HTTPException(status_code=400, detail="order_type must be MKT or LMT")
    if order_type == "LMT" and not body.limit_price:
        raise HTTPException(status_code=400, detail="limit_price is required for LMT orders")
    if body.qty <= 0:
        raise HTTPException(status_code=400, detail="qty must be > 0")

    gw = gateway()
    sym = body.symbol.strip().upper()
    try:
        hit = gw.search_conid(sym)
        if not hit or not hit.get("conid"):
            raise HTTPException(status_code=404, detail=f"No IBKR contract found for '{sym}'.")
        conid = int(hit["conid"])
        order = gw.build_order(conid, side, body.qty, order_type, body.limit_price, body.tif, acct)
        preview = gw.whatif(acct, order)
    except PaperOnlyViolation as e:
        raise _paper_error(e) from e
    except IBKRError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    warnings: list[str] = []
    for key in ("warn", "warning", "error", "message"):
        v = preview.get(key) if isinstance(preview, dict) else None
        if isinstance(v, list):
            warnings.extend(str(x) for x in v)
        elif v:
            warnings.append(str(v))

    oid = new_id("ord_")
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO orders ({','.join(ORDER_COLS)}) VALUES ({','.join('?' for _ in ORDER_COLS)})",
            (oid, now_iso(), body.run_id, acct, sym, conid, side, float(body.qty), order_type,
             body.limit_price, (body.tif or "DAY").upper(), "previewed", None,
             json.dumps({"preview": preview, "order": order}, default=str), None, body.rationale or ""),
        )
    parsed = parse_preview(preview)
    warnings = list(dict.fromkeys(warnings + parsed["warnings"]))
    row = _order_row(oid) or {}
    return {"order_id": oid, "conid": conid, "preview": preview, "warnings": warnings,
            "account_id": acct, "paper": is_paper_account(acct),
            "order": _order_out(row) if row else None}


@router.post("/ibkr/orders/place")
def ibkr_order_place(body: PlaceBody) -> dict[str, Any]:
    row = _order_row(body.order_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown order '{body.order_id}'.")
    if row.get("status") not in {"previewed", "proposed"}:
        raise HTTPException(status_code=409, detail=f"Order '{body.order_id}' is already {row.get('status')}.")
    if not row.get("preview_json"):
        raise HTTPException(status_code=409, detail="Order must be previewed (whatif) before it can be placed.")

    acct = selected_account() or str(row.get("account_id") or "")
    try:
        acct = require_paper_account(acct)
    except PaperOnlyViolation as e:
        raise _paper_error(e) from e
    if str(row.get("account_id") or "") and row["account_id"] != acct:
        raise HTTPException(status_code=409,
                            detail="The selected account changed since this order was previewed. Preview again.")

    try:
        stored = json.loads(row["preview_json"])
        order = stored.get("order") or {}
    except (TypeError, ValueError):
        order = {}
    if not order:
        order = IBKRGateway.build_order(int(row.get("conid") or 0), str(row["side"]), float(row["qty"]),
                                        str(row["order_type"]), row.get("limit_price"),
                                        str(row.get("tif") or "DAY"), acct)

    gw = gateway()
    try:
        result = gw.place(acct, order)
    except PaperOnlyViolation as e:
        raise _paper_error(e) from e
    except IBKRError as e:
        with get_conn() as conn:
            conn.execute("UPDATE orders SET status=?, result_json=? WHERE id=?",
                         ("rejected", json.dumps({"error": str(e)}), row["id"]))
        raise HTTPException(status_code=502, detail=str(e)) from e

    status = "submitted" if result.get("ok") else "rejected"
    broker_id = result.get("order_id") or None
    with get_conn() as conn:
        conn.execute("UPDATE orders SET status=?, broker_order_id=?, result_json=? WHERE id=?",
                     (status, broker_id, json.dumps(result, default=str), row["id"]))
    updated = _order_row(row["id"]) or {}
    return {"order_id": row["id"], "status": status, "broker_order_id": broker_id,
            "messages": result.get("messages") or [], "order_status": result.get("order_status"),
            "order": _order_out(updated) if updated else None}


def _all_order_rows() -> list[dict[str, Any]]:
    with get_conn() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM orders ORDER BY created_at DESC, id DESC").fetchall())


@router.get("/ibkr/orders")
def ibkr_orders(refresh: bool = True) -> dict[str, Any]:
    rows = _all_order_rows()
    if refresh:
        try:
            rows = _refresh_orders(rows)
        except Exception as e:  # noqa: BLE001 - listing must survive a dead gateway
            log.debug("order refresh failed: %s", e)
    return {"orders": [_order_out(r) for r in rows]}


@router.post("/ibkr/orders/refresh")
def ibkr_orders_refresh() -> dict[str, Any]:
    """Force a status/fill/mark refresh from the gateway, then return the list."""
    rows = _all_order_rows()
    try:
        rows = _refresh_orders(rows)
    except Exception as e:  # noqa: BLE001
        log.debug("forced order refresh failed: %s", e)
    return {"orders": [_order_out(r) for r in rows]}


@router.delete("/ibkr/orders/{order_id}")
def ibkr_cancel(order_id: str) -> dict[str, Any]:
    row = _order_row(order_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown order '{order_id}'.")
    acct = str(row.get("account_id") or "") or selected_account()
    try:
        acct = require_paper_account(acct)
    except PaperOnlyViolation as e:
        raise _paper_error(e) from e
    broker_id = str(row.get("broker_order_id") or "")
    result: dict[str, Any] = {}
    if broker_id:
        try:
            result = gateway().cancel(acct, broker_id)
        except PaperOnlyViolation as e:
            raise _paper_error(e) from e
        except IBKRError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
    with get_conn() as conn:
        conn.execute("UPDATE orders SET status=? WHERE id=?", ("cancelled", order_id))
    return {"order_id": order_id, "status": "cancelled", "result": result}


# --------------------------------------------------------------------------- Robinhood
@router.post("/robinhood/login")
def robinhood_login(body: RHLoginBody) -> dict[str, Any]:
    return rh_mod.login(body.username, body.password, body.mfa_code)


@router.get("/robinhood/status")
def robinhood_status() -> dict[str, Any]:
    try:
        return rh_mod.status()
    except Exception as e:  # noqa: BLE001
        return {"logged_in": False, "message": f"Robinhood status unavailable: {e}"}


@router.post("/robinhood/sync")
def robinhood_sync() -> dict[str, Any]:
    try:
        return rh_mod.sync()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Robinhood sync failed: {e}") from e


@router.post("/robinhood/logout")
def robinhood_logout() -> dict[str, Any]:
    return rh_mod.logout()
