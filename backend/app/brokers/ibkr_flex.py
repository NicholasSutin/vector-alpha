"""IBKR Flex Web Service — longer trade history than the gateway's 7 days.

Two-step protocol:
  1. SendRequest(token, queryId)  -> XML with <Status>Success</Status>,
     <ReferenceCode> and <Url> (the GetStatement endpoint to use).
  2. GET {Url}?v=3&t={token}&q={referenceCode} -> the statement.
     While the body says "in progress" (ErrorCode 1009/1019) poll every 5s up to
     60s. ErrorCode 1018 = rate limited -> back off 10s. IBKR allows ~1 req/sec.

The statement is returned as text; if the Flex query is configured with
"CSV" delivery it is CSV, which `app.ingest.ibkr_flex_csv.parse_ibkr_flex_csv`
consumes (imported lazily — another agent owns that module).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

import httpx

from app.brokers.ibkr import USER_AGENT
from app.db import insert_transactions

log = logging.getLogger("vector-alpha.flex")

SEND_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
DEFAULT_GET_URL = "https://gdcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"
FLEX_TIMEOUT = 20.0
POLL_INTERVAL = 5.0
RATE_LIMIT_BACKOFF = 10.0
MAX_WAIT = 60.0

IN_PROGRESS_CODES = {"1009", "1019"}
RATE_LIMIT_CODES = {"1018"}


class FlexError(RuntimeError):
    """Flex Web Service returned an error / never produced a statement."""


def _tag(xml: str, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)</{name}>", xml or "", re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _is_in_progress(body: str) -> bool:
    if not body:
        return False
    low = body.lower()
    if "statement generation in progress" in low or "please try again shortly" in low:
        return True
    return _tag(body, "ErrorCode") in IN_PROGRESS_CODES


def _is_rate_limited(body: str) -> bool:
    return _tag(body, "ErrorCode") in RATE_LIMIT_CODES or "too many request" in (body or "").lower()


def fetch_flex_statement(
    token: str,
    query_id: str,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_wait: float = MAX_WAIT,
) -> str:
    """Run the two-step Flex protocol and return the statement text.

    `client` / `sleep` exist so tests can inject a MockTransport client and skip
    real waiting.
    """
    token = (token or "").strip()
    query_id = (query_id or "").strip()
    if not token or not query_id:
        raise FlexError("Flex token and query id are both required.")

    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=FLEX_TIMEOUT, verify=True,
                              headers={"User-Agent": USER_AGENT})
    try:
        try:
            r = client.get(SEND_URL, params={"v": "3", "t": token, "q": query_id})
        except httpx.HTTPError as e:
            raise FlexError(f"Could not reach the IBKR Flex service: {e}") from e
        if r.status_code >= 400:
            raise FlexError(f"Flex SendRequest failed: HTTP {r.status_code}")
        xml = r.text or ""
        status = _tag(xml, "Status")
        ref = _tag(xml, "ReferenceCode")
        url = _tag(xml, "Url") or DEFAULT_GET_URL
        if status.lower() != "success" or not ref:
            code = _tag(xml, "ErrorCode")
            msg = _tag(xml, "ErrorMessage") or xml[:200]
            raise FlexError(f"Flex SendRequest rejected (code {code or '?'}): {msg}")

        waited = 0.0
        body = ""
        while True:
            try:
                r2 = client.get(url, params={"v": "3", "t": token, "q": ref})
            except httpx.HTTPError as e:
                raise FlexError(f"Could not fetch the Flex statement: {e}") from e
            body = r2.text or ""
            if r2.status_code < 400 and not _is_in_progress(body) and not _is_rate_limited(body):
                break
            delay = RATE_LIMIT_BACKOFF if _is_rate_limited(body) else POLL_INTERVAL
            if waited + delay > max_wait:
                raise FlexError(
                    "IBKR is still generating the Flex statement after "
                    f"{int(max_wait)}s. Try again in a minute."
                )
            sleep(delay)
            waited += delay

        err = _tag(body, "ErrorCode")
        if err and not body.strip().startswith(("ClientAccountID", '"ClientAccountID')):
            msg = _tag(body, "ErrorMessage") or body[:200]
            if "<FlexQueryResponse" not in body:
                raise FlexError(f"Flex statement error (code {err}): {msg}")
        if not body.strip():
            raise FlexError("IBKR returned an empty Flex statement.")
        return body
    finally:
        if owns_client:
            client.close()


def ingest_flex(token: str, query_id: str, account_id: str | None = None,
                client: httpx.Client | None = None,
                sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Fetch + parse + store a Flex statement. Returns an IngestResult dict."""
    text = fetch_flex_statement(token, query_id, client=client, sleep=sleep)
    try:
        from app.ingest.ibkr_flex_csv import parse_ibkr_flex_csv  # lazy: owned by another module
    except Exception as e:  # pragma: no cover - depends on sibling module
        raise NotImplementedError(
            "The IBKR Flex CSV parser (app/ingest/ibkr_flex_csv.py) is not available yet: "
            f"{e}"
        ) from e

    txns = parse_ibkr_flex_csv(text, account_id=account_id) or []
    inserted, skipped = insert_transactions(txns)
    return build_ingest_result("ibkr_flex", txns, inserted, skipped)


def build_ingest_result(source: str, txns: list[dict[str, Any]], inserted: int, skipped: int,
                        warnings: list[str] | None = None) -> dict[str, Any]:
    """Shared IngestResult builder (also used by the live broker syncs)."""
    tss = sorted([str(t.get("ts") or "") for t in txns if t.get("ts")])
    asset_types: dict[str, int] = {}
    accounts: list[str] = []
    for t in txns:
        at = str(t.get("asset_type") or "other")
        asset_types[at] = asset_types.get(at, 0) + 1
        acct = str(t.get("account_id") or "")
        if acct and acct not in accounts:
            accounts.append(acct)
    return {
        "ok": True,
        "source": source,
        "account_ids": accounts,
        "inserted": inserted,
        "skipped_duplicates": skipped,
        "date_range": {"start": tss[0] if tss else None, "end": tss[-1] if tss else None},
        "asset_types": asset_types,
        "warnings": warnings or [],
    }
