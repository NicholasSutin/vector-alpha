"""Robinhood live sync via robin_stocks (all imports lazy).

Login is interactive by nature: robin_stocks calls `builtins.input()` for SMS /
email verification codes and blocks polling while the user approves a push in
the Robinhood app. We therefore:
  * replace `builtins.input` with a function that raises `ChallengeRequired`,
  * run the login in a worker thread with a hard cap,
  * map both cases to `{"status": "challenge", ...}` so the UI can tell the user
    to approve in the app (or re-submit with an `mfa_code`).

Sessions are pickled into `settings.rh_session_dir`, so a later call reuses the
stored session instead of logging in again.
"""
from __future__ import annotations

import builtins
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any

from app.config import settings

log = logging.getLogger("vector-alpha.robinhood")

LOGIN_TIMEOUT_S = 150.0
_state: dict[str, Any] = {"username": "", "logged_in": False, "last_message": ""}


class ChallengeRequired(Exception):
    """robin_stocks asked for interactive input (SMS / email / device code)."""


def _no_input(prompt: str = "") -> str:  # noqa: D401 - stdin is never available here
    raise ChallengeRequired(str(prompt) or "verification code required")


def _totp(mfa_code: str | None) -> str | None:
    """Accept either a 6-digit code or a 32-char base32 TOTP secret."""
    code = (mfa_code or "").strip().replace(" ", "")
    if not code:
        return None
    if len(code) >= 16 and re.fullmatch(r"[A-Z2-7=]+", code.upper()):
        try:
            import pyotp  # lazy

            return pyotp.TOTP(code.upper()).now()
        except Exception as e:  # pragma: no cover - depends on optional dep
            log.warning("TOTP generation failed: %s", e)
            return code
    return code


def _pickle_dir() -> str:
    d = settings.rh_session_dir
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(d)


# --------------------------------------------------------------------------- auth
def login(username: str, password: str, mfa_code: str | None = None) -> dict[str, Any]:
    """Returns {status: ok|challenge|error, message, challenge_type?}."""
    if not username or not password:
        return {"status": "error", "message": "Username and password are required."}

    result: dict[str, Any] = {}

    def _run() -> None:
        original_input = builtins.input
        builtins.input = _no_input  # type: ignore[assignment]
        try:
            from robin_stocks import robinhood as rh  # lazy

            data = rh.login(
                username,
                password,
                mfa_code=_totp(mfa_code),
                store_session=True,
                pickle_path=_pickle_dir(),
            )
            if data and (data.get("access_token") or data.get("detail")):
                result.update({"status": "ok", "message": str(data.get("detail") or "Logged in to Robinhood.")})
                _state.update({"logged_in": True, "username": username})
            else:
                result.update({"status": "error", "message": "Robinhood login returned no session."})
        except ChallengeRequired as e:
            prompt = str(e).lower()
            ctype = "sms" if "sms" in prompt or "code" in prompt else "device_approval"
            result.update({
                "status": "challenge",
                "challenge_type": ctype,
                "message": (
                    "Robinhood needs verification. Approve the login in the Robinhood app, "
                    "then retry — or resubmit with the SMS/email code (or your 32-character "
                    "TOTP secret) in `mfa_code`."
                ),
            })
        except Exception as e:  # noqa: BLE001 - robin_stocks raises bare Exceptions
            msg = str(e)
            low = msg.lower()
            if any(k in low for k in ("challenge", "mfa", "verification", "approve", "unauthorized code")):
                result.update({
                    "status": "challenge",
                    "challenge_type": "mfa",
                    "message": f"Robinhood requires additional verification: {msg}",
                })
            else:
                result.update({"status": "error", "message": f"Robinhood login failed: {msg}"})
        finally:
            builtins.input = original_input  # type: ignore[assignment]

    t = threading.Thread(target=_run, name="rh-login", daemon=True)
    t.start()
    t.join(LOGIN_TIMEOUT_S)
    if t.is_alive():
        out = {
            "status": "challenge",
            "challenge_type": "device_approval",
            "message": (
                "Robinhood is waiting for you to approve this login in the Robinhood app "
                "(Menu -> Security -> device approval). Approve it, then call login again."
            ),
        }
        _state["last_message"] = out["message"]
        return out
    if not result:
        result = {"status": "error", "message": "Robinhood login produced no result."}
    _state["last_message"] = str(result.get("message", ""))
    return result


def status() -> dict[str, Any]:
    """{logged_in, username?, message} — never raises."""
    try:
        from robin_stocks.robinhood import helper as rh_helper  # lazy

        logged_in = bool(getattr(rh_helper, "get_login_state", lambda: False)())
    except Exception as e:  # pragma: no cover
        return {"logged_in": False, "message": f"robin_stocks unavailable: {e}"}

    if not logged_in and _state.get("logged_in"):
        logged_in = True
    if not logged_in:
        return {"logged_in": False, "message": _state.get("last_message") or "Not logged in to Robinhood."}
    return {
        "logged_in": True,
        "username": _state.get("username") or None,
        "message": "Robinhood session active.",
    }


def logout() -> dict[str, Any]:
    try:
        from robin_stocks import robinhood as rh  # lazy

        try:
            rh.logout()
        except Exception:
            pass
        # Drop the pickled session so the next login is clean.
        try:
            for f in settings.rh_session_dir.glob("robinhood*.pickle"):
                f.unlink(missing_ok=True)
        except Exception:
            pass
    except Exception as e:  # pragma: no cover
        return {"ok": False, "message": f"Logout failed: {e}"}
    _state.update({"logged_in": False, "username": "", "last_message": "Logged out."})
    return {"ok": True, "message": "Logged out of Robinhood."}


# --------------------------------------------------------------------------- helpers
def _f(v: Any, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    if isinstance(v, dict):
        v = v.get("amount", default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _iso(v: Any) -> str:
    s = str(v or "").strip()
    if not s:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:26], fmt).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            continue
    return s


def _url_id(url: str | None) -> str:
    parts = [p for p in str(url or "").split("/") if p]
    return parts[-1] if parts else ""


def _fmt_strike(x: float) -> str:
    return str(int(round(x))) if abs(x - round(x)) < 1e-9 else ("%.4f" % x).rstrip("0").rstrip(".")


def _option_symbol(instrument: dict[str, Any]) -> tuple[str, str]:
    """(option symbol `UND YYYY-MM-DD C|P STRIKE`, underlying)."""
    und = str(instrument.get("chain_symbol") or instrument.get("symbol") or "").upper()
    exp = str(instrument.get("expiration_date") or "")[:10]
    cp = "C" if str(instrument.get("type") or "").lower().startswith("c") else "P"
    strike = _f(instrument.get("strike_price"))
    if und and exp:
        return f"{und} {exp} {cp} {_fmt_strike(strike)}", und
    return und, und


# --------------------------------------------------------------------------- sync
def sync(account_id: str = "robinhood") -> dict[str, Any]:
    """Pull orders / dividends / transfers / interest -> normalized transactions."""
    from app.brokers.ibkr_flex import build_ingest_result  # local import: shared builder
    from app.db import insert_transactions

    try:
        from robin_stocks import robinhood as rh  # lazy
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"robin_stocks is not installed: {e}") from e

    warnings: list[str] = []
    txns: list[dict[str, Any]] = []
    sym_cache: dict[str, str] = {}
    opt_cache: dict[str, dict[str, Any]] = {}

    def symbol_for(url: str | None) -> str:
        u = str(url or "")
        if not u:
            return ""
        if u not in sym_cache:
            try:
                sym_cache[u] = str(rh.get_symbol_by_url(u) or "").upper()
            except Exception:
                sym_cache[u] = ""
        return sym_cache[u]

    def option_for(url_or_id: str) -> dict[str, Any]:
        oid = _url_id(url_or_id) or str(url_or_id or "")
        if not oid:
            return {}
        if oid not in opt_cache:
            try:
                data = rh.get_option_instrument_data_by_id(oid)
                opt_cache[oid] = data if isinstance(data, dict) else {}
            except Exception:
                opt_cache[oid] = {}
        return opt_cache[oid]

    def pull(name: str):
        fn = getattr(rh, name, None)
        if fn is None:
            warnings.append(f"robin_stocks has no {name}()")
            return []
        try:
            data = fn()
        except Exception as e:  # noqa: BLE001
            warnings.append(f"{name} failed: {e}")
            return []
        return [d for d in (data or []) if isinstance(d, dict)]

    # --- stock orders ---
    for o in pull("get_all_stock_orders"):
        state = str(o.get("state") or "").lower()
        if state not in {"filled", "partially_filled"}:
            continue
        qty = _f(o.get("cumulative_quantity"), _f(o.get("quantity")))
        price = _f(o.get("average_price"), _f(o.get("price")))
        if qty <= 0:
            continue
        fees = abs(_f(o.get("fees"))) + abs(_f(o.get("regulatory_fees")))
        side = "buy" if str(o.get("side") or "").lower().startswith("b") else "sell"
        sym = symbol_for(o.get("instrument"))
        gross = qty * price
        txns.append({
            "id": f"rhl:{o.get('id')}",
            "source": "robinhood_live", "account_id": account_id,
            "ts": _iso(o.get("last_transaction_at") or o.get("updated_at") or o.get("created_at")),
            "symbol": sym, "underlying": sym, "asset_type": "stock",
            "side": side, "open_close": "", "qty": qty, "price": price, "fees": fees,
            "amount": (-gross - fees) if side == "buy" else (gross - fees),
            "description": f"{side.upper()} {qty:g} {sym} @ {price:g}",
            "external_id": str(o.get("id") or ""), "strategy_tag": "", "raw_json": o,
        })

    # --- option orders (per leg) ---
    for o in pull("get_all_option_orders"):
        state = str(o.get("state") or "").lower()
        if state not in {"filled", "partially_filled"}:
            continue
        legs = [l for l in (o.get("legs") or []) if isinstance(l, dict)]
        premium = abs(_f(o.get("processed_premium")))
        order_qty = _f(o.get("processed_quantity"), _f(o.get("quantity")))
        fees = abs(_f(o.get("regulatory_fees"))) + abs(_f(o.get("opening_strategy_fees")))
        ts = _iso(o.get("updated_at") or o.get("created_at"))
        for i, leg in enumerate(legs):
            inst = option_for(leg.get("option") or leg.get("option_id") or "")
            sym, und = _option_symbol(inst) if inst else (str(o.get("chain_symbol") or "").upper(),
                                                          str(o.get("chain_symbol") or "").upper())
            execs = [e for e in (leg.get("executions") or []) if isinstance(e, dict)]
            qty = sum(_f(e.get("quantity")) for e in execs) or order_qty
            price = (sum(_f(e.get("price")) * _f(e.get("quantity")) for e in execs) / qty
                     if execs and qty else _f(o.get("price")))
            side = "buy" if str(leg.get("side") or "").lower().startswith("b") else "sell"
            eff = str(leg.get("position_effect") or "").lower()
            open_close = "open" if eff.startswith("o") else ("close" if eff.startswith("c") else "")
            gross = (premium / len(legs)) if (premium and len(legs)) else qty * price * 100.0
            leg_fees = fees / max(len(legs), 1)
            txns.append({
                "id": f"rhl:{o.get('id')}:L{i}",
                "source": "robinhood_live", "account_id": account_id, "ts": ts,
                "symbol": sym or str(o.get("chain_symbol") or "").upper(),
                "underlying": und or str(o.get("chain_symbol") or "").upper(),
                "asset_type": "option", "side": side, "open_close": open_close,
                "qty": qty, "price": price, "fees": leg_fees,
                "amount": (-gross - leg_fees) if side == "buy" else (gross - leg_fees),
                "description": f"{side.upper()} {open_close} {qty:g} {sym}",
                "external_id": str(o.get("id") or ""), "strategy_tag": "", "raw_json": {**o, "_leg": i},
            })

    # --- crypto orders ---
    pairs: dict[str, str] = {}
    crypto_orders = pull("get_all_crypto_orders")
    if crypto_orders:
        try:
            for p in rh.get_crypto_currency_pairs() or []:
                if isinstance(p, dict) and p.get("id"):
                    code = (p.get("asset_currency") or {}).get("code") or p.get("symbol") or ""
                    pairs[str(p["id"])] = str(code).upper().replace("-USD", "")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"crypto pair lookup failed: {e}")
    for o in crypto_orders:
        state = str(o.get("state") or "").lower()
        if state not in {"filled", "partially_filled"}:
            continue
        qty = _f(o.get("cumulative_quantity"), _f(o.get("quantity")))
        price = _f(o.get("average_price"), _f(o.get("price")))
        if qty <= 0:
            continue
        side = "buy" if str(o.get("side") or "").lower().startswith("b") else "sell"
        sym = pairs.get(str(o.get("currency_pair_id") or ""), "") or str(o.get("currency_pair_id") or "")[:8]
        gross = qty * price
        txns.append({
            "id": f"rhl:{o.get('id')}", "source": "robinhood_live", "account_id": account_id,
            "ts": _iso(o.get("last_transaction_at") or o.get("updated_at") or o.get("created_at")),
            "symbol": sym, "underlying": sym, "asset_type": "crypto", "side": side, "open_close": "",
            "qty": qty, "price": price, "fees": 0.0,
            "amount": -gross if side == "buy" else gross,
            "description": f"{side.upper()} {qty:g} {sym} @ {price:g}",
            "external_id": str(o.get("id") or ""), "strategy_tag": "", "raw_json": o,
        })

    # --- dividends ---
    for d in pull("get_dividends"):
        if str(d.get("state") or "").lower() in {"voided", "reverted", "cancelled"}:
            continue
        amt = _f(d.get("amount"))
        sym = symbol_for(d.get("instrument"))
        txns.append({
            "id": f"rhl:{d.get('id')}", "source": "robinhood_live", "account_id": account_id,
            "ts": _iso(d.get("paid_at") or d.get("payable_date") or d.get("record_date")),
            "symbol": sym, "underlying": sym, "asset_type": "dividend", "side": "none",
            "open_close": "", "qty": _f(d.get("position")), "price": _f(d.get("rate")),
            "fees": 0.0, "amount": amt, "description": f"Dividend {sym}",
            "external_id": str(d.get("id") or ""), "strategy_tag": "", "raw_json": d,
        })

    # --- bank transfers ---
    for tr in pull("get_bank_transfers"):
        if str(tr.get("state") or "").lower() in {"cancelled", "failed", "reversed"}:
            continue
        amt = abs(_f(tr.get("amount")))
        direction = str(tr.get("direction") or "").lower()
        signed = amt if direction.startswith("deposit") else -amt
        txns.append({
            "id": f"rhl:{tr.get('id')}", "source": "robinhood_live", "account_id": account_id,
            "ts": _iso(tr.get("created_at") or tr.get("updated_at")),
            "symbol": "", "underlying": "", "asset_type": "transfer", "side": "none",
            "open_close": "", "qty": 0.0, "price": 0.0, "fees": 0.0, "amount": signed,
            "description": f"Bank transfer ({direction or 'unknown'})",
            "external_id": str(tr.get("id") or ""), "strategy_tag": "", "raw_json": tr,
        })

    # --- interest ---
    for ip in pull("get_interest_payments"):
        amt = _f(ip.get("amount"))
        txns.append({
            "id": f"rhl:{ip.get('id')}", "source": "robinhood_live", "account_id": account_id,
            "ts": _iso(ip.get("payout_date") or ip.get("created_at") or ip.get("updated_at")),
            "symbol": "", "underlying": "", "asset_type": "interest", "side": "none",
            "open_close": "", "qty": 0.0, "price": 0.0, "fees": 0.0, "amount": amt,
            "description": "Interest payment", "external_id": str(ip.get("id") or ""),
            "strategy_tag": "", "raw_json": ip,
        })

    # Robinhood returns newest-first; analytics prefer chronological order.
    txns.sort(key=lambda t: str(t.get("ts") or ""))
    inserted, skipped = insert_transactions(txns)
    return build_ingest_result("robinhood_live", txns, inserted, skipped, warnings)
