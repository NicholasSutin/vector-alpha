"""In-process run event bus: buffered events + live fan-out to SSE subscribers."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from app.db import get_conn

log = logging.getLogger("vector-alpha.events")

TERMINAL = {"final", "error"}


class RunBus:
    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._subs: dict[str, list[asyncio.Queue]] = {}
        self._done: set[str] = set()

    # ------------------------------------------------------------------
    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        buf = self._events.setdefault(run_id, [])
        buf.append(event)
        if event.get("type") in TERMINAL:
            self._done.add(run_id)
        for q in list(self._subs.get(run_id, [])):
            try:
                q.put_nowait(event)
            except Exception:  # pragma: no cover - queue is unbounded
                pass

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return list(self._events.get(run_id, []))

    def is_done(self, run_id: str) -> bool:
        return run_id in self._done

    async def subscribe(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        """Replay buffered events, then stream live ones. Ends after final/error."""
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(run_id, []).append(q)
        try:
            replayed = self.events(run_id)
            for ev in replayed:
                yield ev
                if ev.get("type") in TERMINAL:
                    return
            seen = len(replayed)
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    if self.is_done(run_id):
                        return
                    continue
                # a subscriber that joined mid-publish may re-see replayed items
                buf = self._events.get(run_id, [])
                if seen < len(buf) and ev is buf[seen]:
                    seen += 1
                yield ev
                if ev.get("type") in TERMINAL:
                    return
        finally:
            subs = self._subs.get(run_id, [])
            if q in subs:
                subs.remove(q)

    # ------------------------------------------------------------------
    def persist(self, run_id: str) -> None:
        """Write the buffered events into runs.events_json (called at run end)."""
        try:
            payload = json.dumps(self.events(run_id), default=str)
            with get_conn() as conn:
                conn.execute("UPDATE runs SET events_json=? WHERE id=?", (payload, run_id))
        except Exception as e:
            log.warning("persist events failed for %s: %s", run_id, e)

    def clear(self, run_id: str) -> None:
        self._events.pop(run_id, None)
        self._subs.pop(run_id, None)
        self._done.discard(run_id)


bus = RunBus()
