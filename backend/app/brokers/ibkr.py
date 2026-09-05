"""IBKR Client Portal Gateway client (paper only) + normalizers.

The gateway is a local Java process serving a self-signed HTTPS endpoint
(default https://localhost:5001/v1/api). Everything here is sync httpx; the
routes call it from FastAPI's threadpool.

Nothing in this module places an order without the caller having gone through
`app.brokers.guard.require_paper_account`.
"""
from __future__ import annotations

import json
import logging
import re
import time
import warnings
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from app.config import settings
from app.db import get_conn, new_id, now_iso
from app.brokers.guard import require_paper_account

log = logging.getLogger("vector-alpha.ibkr")

USER_AGENT = "vector-alpha/0.1"
DEFAULT_TIMEOUT = 10.0

# The gateway uses a self-signed cert; we talk to localhost only.
warnings.filterwarnings("ignore", message=".*Unverified HTTPS request.*")
try:  # urllib3 may not be installed; requests-based libs (robin_stocks) pull it in
    import urllib3  # type: ignore

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:  # pragma: no cover - optional dependency
    pass


class IBKRError(RuntimeError):
    """Gateway returned an error or could not be reached."""


# --------------------------------------------------------------------------- helpers
def _num(v: Any, default: float = 0.0) -> float:
    """Parse a possibly-decorated numeric string ('C123.45', '1,234', '' )."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return default
    s = s.replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return default
    try:
        return float(m.group(0))
    except ValueError:
        return default


MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def _fmt_strike(strike: float) -> str:
    if abs(strike - round(strike)) < 1e-9:
        return str(int(round(strike)))
    return ("%.4f" % strike).rstrip("0").rstrip(".")


def parse_option_symbol(description: str, underlying: str = "") -> str:
    """Best-effort `UNDERLYING YYYY-MM-DD C|P STRIKE` from an IBKR contract description.

    Handles the common shapes:
      "AAPL  260619C00150000" / "AAPL 20260619C00150000"   (OCC)
      "NVDA JUN 20 '26 140 Call"                            (contractDesc)
      "NVDA 20JUN26 140 C"
    Returns "" when nothing parses.
    """
    if not description:
        return ""
    d = str(description).strip().upper()
    root = (underlying or d.split()[0] if d.split() else "").upper()

    # OCC style
    m = re.search(r"\b([A-Z][A-Z0-9.]{0,5})?\s*(\d{6}|\d{8})\s*([CP])\s*(\d{8})\b", d)
    if m:
        sym = m.group(1) or root
        ymd, cp, strike_raw = m.group(2), m.group(3), m.group(4)
        if len(ymd) == 6:
            year, month, day = 2000 + int(ymd[0:2]), int(ymd[2:4]), int(ymd[4:6])
        else:
            year, month, day = int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8])
        strike = int(strike_raw) / 1000.0
        return f"{sym} {year:04d}-{month:02d}-{day:02d} {cp} {_fmt_strike(strike)}"

    # "JUN 20 '26 140 Call" / "JUN 20 26 140 C"
    m = re.search(r"\b([A-Z]{3})\s*(\d{1,2})\s*'?(\d{2,4})\s+([\d.]+)\s*(CALL|PUT|C|P)\b", d)
    if m and m.group(1) in MONTHS:
        month = MONTHS[m.group(1)]
        day = int(m.group(2))
        y = int(m.group(3))
        year = y if y > 1000 else 2000 + y
        strike = _num(m.group(4))
        cp = "C" if m.group(5).startswith("C") else "P"
        return f"{root} {year:04d}-{month:02d}-{day:02d} {cp} {_fmt_strike(strike)}"

    # "20JUN26 140 C"
    m = re.search(r"\b(\d{1,2})([A-Z]{3})(\d{2,4})\s+([\d.]+)\s*(CALL|PUT|C|P)\b", d)
    if m and m.group(2) in MONTHS:
        day = int(m.group(1))
        month = MONTHS[m.group(2)]
        y = int(m.group(3))
        year = y if y > 1000 else 2000 + y
        strike = _num(m.group(4))
        cp = "C" if m.group(5).startswith("C") else "P"
        return f"{root} {year:04d}-{month:02d}-{day:02d} {cp} {_fmt_strike(strike)}"
    return ""


def _asset_type(sec_type: str | None, asset_class: str | None = None) -> str:
    v = (sec_type or asset_class or "").upper()
    if v.startswith("OPT") or v in {"FOP", "WAR"}:
        return "option"
    if v.startswith("STK") or v in {"ETF", "IND"}:
        return "stock"
    if v.startswith("CRYPTO") or v == "CRY":
        return "crypto"
    if v in {"CASH", "FUND"}:
        return "other"
    return "stock" if not v else "other"


def _ts_iso(trade: dict[str, Any]) -> str:
    """ISO timestamp from trade_time_r (epoch ms) or trade_time ('20260705-14:31:02')."""
    r = trade.get("trade_time_r") or trade.get("tradeTime_r")
    try:
        if r:
            ms = float(r)
            if ms > 1e11:  # milliseconds
                ms /= 1000.0
            return datetime.fromtimestamp(ms, tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        pass
    raw = str(trade.get("trade_time") or trade.get("tradeTime") or "").strip()
    for fmt in ("%Y%m%d-%H:%M:%S", "%Y%m%d-%H%M%S", "%Y%m%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            continue
    return now_iso()


# --------------------------------------------------------------------------- client
class IBKRGateway:
    """Thin sync client over the Client Portal Gateway REST API.

    `transport` lets tests inject an `httpx.MockTransport`.
    """

    def __init__(
        self,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = (base_url or settings.ibkr_gateway_url or "https://localhost:5001/v1/api").rstrip("/")
        self._transport = transport
        self.timeout = timeout
        self._primed_iserver = False
        self._primed_portfolio = False

    # -- plumbing -----------------------------------------------------------
    @property
    def origin(self) -> str:
        """Browser URL for the gateway login page, e.g. https://localhost:5001."""
        m = re.match(r"^(https?://[^/]+)", self.base_url)
        return m.group(1) if m else self.base_url

    def _client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "timeout": self.timeout,
            "headers": {"User-Agent": USER_AGENT, "Accept": "application/json"},
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        else:
            kwargs["verify"] = False
        return httpx.Client(**kwargs)

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            with self._client() as c:
                resp = c.request(method, url, **kw)
        except httpx.HTTPError as e:
            raise IBKRError(f"IBKR gateway unreachable at {self.base_url}: {e}") from e
        if resp.status_code >= 400:
            body = (resp.text or "")[:300]
            raise IBKRError(f"IBKR {method} {path} -> HTTP {resp.status_code}: {body}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError):
            return {"raw": resp.text}

    def _get(self, path: str, **kw: Any) -> Any:
        return self._request("GET", path, **kw)

    def _post(self, path: str, **kw: Any) -> Any:
        return self._request("POST", path, **kw)

    # -- priming ------------------------------------------------------------
    # The gateway requires /iserver/accounts before any /iserver order or
    # market-data call, and /portfolio/accounts before any /portfolio/* call.
    def _prime_iserver(self) -> None:
        if self._primed_iserver:
            return
        self._primed_iserver = True
        try:
            self._get("/iserver/accounts")
        except IBKRError as e:  # non-fatal: the real call will report the failure
            log.debug("iserver prime failed: %s", e)

    def _prime_portfolio(self) -> None:
        if self._primed_portfolio:
            return
        self._primed_portfolio = True
        try:
            self._get("/portfolio/accounts")
        except IBKRError as e:
            log.debug("portfolio prime failed: %s", e)

    # -- session ------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        data = self._post("/iserver/auth/status")
        if not isinstance(data, dict):
            data = {}
        return {
            "authenticated": bool(data.get("authenticated")),
            "connected": bool(data.get("connected")),
            "competing": bool(data.get("competing")),
            "message": data.get("message") or (data.get("fail") or ""),
        }

    def tickle(self) -> dict[str, Any]:
        try:
            out = self._post("/tickle")
            return out if isinstance(out, dict) else {}
        except IBKRError:
            return {}

    def accounts(self) -> list[str]:
        """Account ids from /portfolio/accounts, falling back to /iserver/accounts."""
        ids: list[str] = []
        self._primed_portfolio = True
        try:
            data = self._get("/portfolio/accounts")
            if isinstance(data, list):
                for a in data:
                    if isinstance(a, dict):
                        acct = a.get("accountId") or a.get("id") or a.get("acctId")
                        if acct:
                            ids.append(str(acct))
                    elif a:
                        ids.append(str(a))
        except IBKRError:
            pass
        if not ids:
            self._primed_iserver = True
            data = self._get("/iserver/accounts")
            if isinstance(data, dict):
                ids = [str(a) for a in (data.get("accounts") or [])]
        # de-dup, preserve order
        seen: set[str] = set()
        return [a for a in ids if not (a in seen or seen.add(a))]

    def selected_account(self) -> str | None:
        self._primed_iserver = True
        try:
            data = self._get("/iserver/accounts")
            if isinstance(data, dict) and data.get("selectedAccount"):
                return str(data["selectedAccount"])
        except IBKRError:
            pass
        return None

    # -- portfolio ----------------------------------------------------------
    PAGE_SIZE = 100

    def positions(self, account_id: str, max_pages: int = 10) -> list[dict[str, Any]]:
        self._prime_portfolio()
        out: list[dict[str, Any]] = []
        for page in range(max_pages):
            data = self._get(f"/portfolio/{account_id}/positions/{page}")
            if not isinstance(data, list) or not data:
                break
            out.extend([p for p in data if isinstance(p, dict)])
            if len(data) < self.PAGE_SIZE:
                break
        return out

    def summary(self, account_id: str) -> dict[str, Any]:
        self._prime_portfolio()
        data = self._get(f"/portfolio/{account_id}/summary")
        return data if isinstance(data, dict) else {}

    def ledger(self, account_id: str) -> dict[str, Any]:
        self._prime_portfolio()
        try:
            data = self._get(f"/portfolio/{account_id}/ledger")
            return data if isinstance(data, dict) else {}
        except IBKRError:
            return {}

    def trades(self, days: int = 7) -> list[dict[str, Any]]:
        days = max(1, min(int(days or 7), 7))  # gateway caps at 7
        self._prime_iserver()
        data = self._get("/iserver/account/trades", params={"days": days})
        if isinstance(data, list):
            return [t for t in data if isinstance(t, dict)]
        return []

    # -- market data --------------------------------------------------------
    def search_conid(self, symbol: str, sec_type: str = "STK") -> dict[str, Any] | None:
        sym = (symbol or "").strip().upper()
        if not sym:
            return None
        self._prime_iserver()
        data = self._get("/iserver/secdef/search", params={"symbol": sym, "secType": sec_type})
        if not isinstance(data, list):
            return None
        rows = [r for r in data if isinstance(r, dict)]
        exact = [r for r in rows if str(r.get("symbol", "")).upper() == sym]
        for r in exact:
            sections = r.get("sections") or []
            if any(str(s.get("secType", "")).upper() == sec_type.upper() for s in sections if isinstance(s, dict)):
                return r
        return (exact or rows or [None])[0]

    def snapshot(self, conid: int | str, fields: str = "31,84,86", prime: bool = True,
                 prime_delay: float = 0.7) -> dict[str, Any]:
        """Market data snapshot. The first call only primes the subscription."""
        self._prime_iserver()
        params = {"conids": str(conid), "fields": fields}
        raw: Any = []
        attempts = 2 if prime else 1
        for i in range(attempts):
            raw = self._get("/iserver/marketdata/snapshot", params=params)
            if i + 1 < attempts and prime_delay:
                time.sleep(prime_delay)
        row = raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], dict) else {}
        last = row.get("31")
        bid = row.get("84")
        ask = row.get("86")
        return {
            "conid": int(_num(conid)) if str(conid).strip() else None,
            "last": _num(last, 0.0) or None,
            "bid": _num(bid, 0.0) or None,
            "ask": _num(ask, 0.0) or None,
            "raw": row,
        }

    # -- orders (guarded) ---------------------------------------------------
    @staticmethod
    def build_order(conid: int, side: str, qty: float, order_type: str = "MKT",
                    limit_price: float | None = None, tif: str = "DAY",
                    account_id: str = "") -> dict[str, Any]:
        order: dict[str, Any] = {
            "conid": int(conid),
            "orderType": (order_type or "MKT").upper(),
            "side": (side or "BUY").upper(),
            "quantity": float(qty),
            "tif": (tif or "DAY").upper(),
        }
        if order["orderType"] == "LMT":
            order["price"] = float(limit_price or 0)
        if account_id:
            order["acctId"] = account_id
        return order

    def whatif(self, account_id: str, order: dict[str, Any]) -> dict[str, Any]:
        acct = require_paper_account(account_id)
        self._prime_iserver()
        data = self._post(f"/iserver/account/{acct}/orders/whatif", json={"orders": [order]})
        if isinstance(data, list):
            data = data[0] if data and isinstance(data[0], dict) else {}
        return data if isinstance(data, dict) else {}

    def reply(self, reply_id: str, confirmed: bool = True) -> Any:
        return self._post(f"/iserver/reply/{reply_id}", json={"confirmed": bool(confirmed)})

    def place(self, account_id: str, order: dict[str, Any], max_replies: int = 3) -> dict[str, Any]:
        """Place an order, auto-confirming IBKR's questions up to `max_replies` times.

        Returns {ok, order_id, order_status, messages, raw}.
        """
        acct = require_paper_account(account_id)
        self._prime_iserver()
        data = self._post(f"/iserver/account/{acct}/orders", json={"orders": [order]})
        messages: list[str] = []
        for _ in range(max_replies + 1):
            rows = data if isinstance(data, list) else [data]
            rows = [r for r in rows if isinstance(r, dict)]
            if not rows:
                break
            row = rows[0]
            reply_id = row.get("id") if row.get("message") else None
            if reply_id and not row.get("order_id"):
                msgs = row.get("message") or []
                messages.extend([str(m) for m in msgs] if isinstance(msgs, list) else [str(msgs)])
                data = self.reply(str(reply_id))
                continue
            return {
                "ok": True,
                "order_id": str(row.get("order_id") or row.get("orderId") or ""),
                "order_status": str(row.get("order_status") or row.get("status") or "submitted"),
                "messages": messages,
                "raw": rows,
            }
        return {"ok": False, "order_id": "", "order_status": "unconfirmed",
                "messages": messages + ["Too many confirmation prompts from IBKR; order not confirmed."],
                "raw": data}

    def order_status(self, order_id: str) -> dict[str, Any]:
        """Per-order status: {order_status, average_price, filled_quantity, last_execution_time,...}."""
        self._prime_iserver()
        data = self._get(f"/iserver/account/order/status/{order_id}")
        return data if isinstance(data, dict) else {}

    def live_orders(self) -> list[dict[str, Any]]:
        try:
            data = self._get("/iserver/account/orders")
        except IBKRError:
            return []
        if isinstance(data, dict):
            orders = data.get("orders") or []
        elif isinstance(data, list):
            orders = data
        else:
            orders = []
        return [o for o in orders if isinstance(o, dict)]

    def cancel(self, account_id: str, order_id: str) -> dict[str, Any]:
        acct = require_paper_account(account_id)
        data = self._request("DELETE", f"/iserver/account/{acct}/order/{order_id}")
        return data if isinstance(data, dict) else {"raw": data}


def ib_time_to_iso(value: Any) -> str | None:
    """'20260705-14:31:02' / epoch ms / ISO -> ISO 8601 (None when unparseable)."""
    if value in (None, ""):
        return None
    try:
        num = float(value)
        if num > 1e8:
            if num > 1e11:
                num /= 1000.0
            return datetime.fromtimestamp(num, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        pass
    raw = str(value).strip()
    for fmt in ("%Y%m%d-%H:%M:%S", "%Y%m%d-%H%M%S", "%y%m%d-%H:%M:%S", "%Y%m%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            continue
    return raw or None


# --------------------------------------------------------------------------- converters
def trades_to_transactions(trades: Iterable[dict[str, Any]], account_id: str = "") -> list[dict[str, Any]]:
    """IBKR execution rows -> normalized `transactions` rows (source `ibkr_live`)."""
    out: list[dict[str, Any]] = []
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        exec_id = str(t.get("execution_id") or t.get("executionId") or t.get("execid") or "").strip()
        raw_side = str(t.get("side") or "").upper()
        side = "buy" if raw_side.startswith("B") else ("sell" if raw_side.startswith("S") else "none")
        sec_type = str(t.get("sec_type") or t.get("secType") or "").upper()
        asset_type = _asset_type(sec_type)
        mult = 100.0 if asset_type == "option" else 1.0

        raw_symbol = str(t.get("symbol") or t.get("ticker") or "").upper()
        desc = str(t.get("contract_description_1") or t.get("contractDesc")
                   or t.get("company_name") or t.get("description") or "")
        underlying = raw_symbol or (desc.split()[0].upper() if desc.split() else "")
        symbol = raw_symbol
        if asset_type == "option":
            symbol = parse_option_symbol(desc, underlying) or parse_option_symbol(raw_symbol, underlying) or raw_symbol

        qty = abs(_num(t.get("size") or t.get("quantity")))
        price = _num(t.get("price"))
        fees = abs(_num(t.get("commission")))
        net = t.get("net_amount", t.get("netAmount"))
        gross = abs(_num(net)) if net not in (None, "") else qty * price * mult
        amount = (-gross - fees) if side == "buy" else ((gross - fees) if side == "sell" else -fees)

        out.append({
            "id": f"ibkr:{exec_id}" if exec_id else new_id("ibkr_"),
            "source": "ibkr_live",
            "account_id": str(t.get("account") or t.get("acctId") or account_id or ""),
            "ts": _ts_iso(t),
            "symbol": symbol,
            "underlying": underlying,
            "asset_type": asset_type,
            "side": side,
            "open_close": "",
            "qty": qty,
            "price": price,
            "fees": fees,
            "amount": amount,
            "description": desc or raw_symbol,
            "external_id": str(t.get("order_ref") or t.get("orderId") or exec_id or ""),
            "strategy_tag": "",
            "raw_json": json.dumps(t, default=str),
        })
    return out


def positions_to_models(positions: Iterable[dict[str, Any]], account_id: str = "") -> list[dict[str, Any]]:
    """IBKR portfolio positions -> `models.Position` dicts."""
    out: list[dict[str, Any]] = []
    for p in positions or []:
        if not isinstance(p, dict):
            continue
        asset_class = str(p.get("assetClass") or p.get("sec_type") or "").upper()
        asset_type = _asset_type(asset_class)
        ticker = str(p.get("ticker") or p.get("contractDesc") or "").split()[0].upper() if (
            p.get("ticker") or p.get("contractDesc")) else ""
        desc = str(p.get("contractDesc") or p.get("name") or "")
        symbol = ticker
        if asset_type == "option":
            symbol = parse_option_symbol(desc, ticker) or ticker or desc
        qty = _num(p.get("position"))
        out.append({
            "symbol": symbol or desc,
            "underlying": ticker or (desc.split()[0].upper() if desc.split() else ""),
            "asset_type": asset_type,
            "qty": qty,
            "avg_cost": _num(p.get("avgCost") or p.get("avgPrice")),
            "market_value": _num(p.get("mktValue"), 0.0) if p.get("mktValue") not in (None, "") else None,
            "unrealized_pnl": _num(p.get("unrealizedPnl"), 0.0) if p.get("unrealizedPnl") not in (None, "") else None,
            "account_id": str(p.get("acctId") or account_id or ""),
            "source": "ibkr_live",
        })
    return out


def summary_equity_cash(summary: dict[str, Any]) -> tuple[float | None, float | None]:
    """Pull (netliquidation, totalcashvalue) out of a /portfolio/{acct}/summary payload."""
    def pick(*keys: str) -> float | None:
        for k in keys:
            for candidate in (k, k.upper(), k.lower()):
                v = (summary or {}).get(candidate)
                if isinstance(v, dict):
                    v = v.get("amount")
                if v not in (None, ""):
                    return _num(v)
        return None

    return pick("netliquidation", "NetLiquidation"), pick("totalcashvalue", "TotalCashValue")


def save_snapshot(account_id: str, positions: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    """Persist the latest positions/equity snapshot. Returns the snapshot id."""
    equity, cash = summary_equity_cash(summary or {})
    sid = new_id("snap_")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO snapshots (id, source, account_id, as_of, equity, cash, positions_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (sid, "ibkr_live", account_id or "", now_iso(), equity, cash,
             json.dumps(positions, default=str)),
        )
    return sid
