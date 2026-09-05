"""PRISM (Block Convey) tracing wrapper.

Auth header is `X-PRISMtrace-Key` (the SDK sets it) — never `Authorization: Bearer`.
Every failure is swallowed: PRISM must never break an analysis run.

Usage:
    from app.integrations.prism import prism
    prism.trace_llm(model=..., input_messages=[...], output="...", latency_ms=12, run_id=run_id)
    prism.submit_trajectory(steps, run_id=run_id, model="...", final_status="success")
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("vector-alpha.prism")

DASHBOARD_URL = "https://prism.blockconvey.com"


class PrismClient:
    """Thin, never-throwing wrapper around `prismtrace.PRISMtrace`."""

    def __init__(self) -> None:
        self.enabled: bool = False
        self._client: Any = None
        self.agent_name = settings.prism_agent_name
        self.agent_id = settings.prism_agent_id
        if settings.has_prism:
            try:
                from prismtrace import PRISMtrace  # type: ignore

                self._client = PRISMtrace(
                    api_key=settings.prism_api_key,
                    host=settings.prism_host,
                    project_id=settings.prism_project_id,
                )
                self.enabled = True
            except Exception as e:  # pragma: no cover - import/network guard
                log.warning("PRISM disabled: %s", e)
                self.enabled = False

    # ------------------------------------------------------------------
    def trace_llm(
        self,
        *,
        model: str,
        input_messages: list[dict[str, Any]],
        output: str,
        latency_ms: int,
        run_id: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Record one model call. `session_id` == run_id so PRISM assembles a trajectory."""
        if not self.enabled or self._client is None:
            return False
        meta = dict(metadata or {})
        meta.setdefault("session_id", run_id)          # top-level-equivalent for older callers
        meta.setdefault("agent_id", self.agent_id)
        meta.setdefault("agent_name", self.agent_name)
        try:
            self._client.trace_llm(
                model=model,
                input_messages=input_messages,
                output=output or "",
                latency_ms=int(latency_ms),
                token_count_input=int(tokens_in or 0),
                token_count_output=int(tokens_out or 0),
                trace_id=None,
                agent_id=self.agent_id,
                agent_name=self.agent_name,
                metadata=meta,
            )
            return True
        except Exception as e:
            log.warning("PRISM trace_llm failed: %s", e)
            return False

    def submit_trajectory(
        self,
        steps: list[dict[str, Any]],
        run_id: str,
        model: str,
        final_status: str = "success",
    ) -> bool:
        if not self.enabled or self._client is None:
            return False
        try:
            self._client.submit_trajectory(
                steps,
                agent_name=self.agent_name,
                agent_id=self.agent_id,
                conversation_id=run_id,   # == session_id == run_id
                request_id=run_id,
                model=model,
                final_status=final_status,
                async_send=False,
            )
            return True
        except Exception as e:
            log.warning("PRISM submit_trajectory failed: %s", e)
            return False

    def flush(self, timeout: float = 3.0) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            self._client.flush(timeout)
        except Exception as e:  # pragma: no cover
            log.warning("PRISM flush failed: %s", e)

    # ------------------------------------------------------------------
    def doctor(self) -> dict[str, Any]:
        """GET {host}/api/setup-doctor?project_id=... — shape per docs/API.md `/prism/status`."""
        base = {
            "configured": bool(settings.has_prism),
            "host": settings.prism_host,
            "project_id": settings.prism_project_id or None,
            "credential_ok": False,
            "live_connected": False,
            "blocked_step": None,
            "live_trace_count": 0,
            "dashboard_url": DASHBOARD_URL,
        }
        if not settings.has_prism:
            return base
        try:
            r = httpx.get(
                f"{settings.prism_host.rstrip('/')}/api/setup-doctor",
                params={"project_id": settings.prism_project_id},
                headers={"X-PRISMtrace-Key": settings.prism_api_key},
                timeout=10.0,
            )
            if r.status_code == 200:
                d = r.json() if isinstance(r.json(), dict) else {}
                base["credential_ok"] = True
                base["live_connected"] = bool(d.get("live_connected"))
                base["blocked_step"] = d.get("blocked_step")
                base["live_trace_count"] = int(
                    d.get("live_trace_count") or d.get("trace_count") or 0
                )
                base["raw"] = d
            else:
                base["blocked_step"] = f"http_{r.status_code}"
                base["error"] = r.text[:200]
        except Exception as e:
            base["blocked_step"] = "unreachable"
            base["error"] = str(e)[:200]
        return base


prism = PrismClient()
