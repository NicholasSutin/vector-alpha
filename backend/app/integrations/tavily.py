"""Tavily web search. Returns [] when unconfigured or on any error."""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings

log = logging.getLogger("vector-alpha.tavily")


def web_search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search news for `query`. Never raises. -> [{title, url, content}]"""
    if not settings.has_tavily or not query:
        return []
    try:
        from tavily import TavilyClient  # type: ignore

        client = TavilyClient(api_key=settings.tavily_api_key)
        resp = client.search(
            query,
            max_results=max_results,
            topic="news",
            search_depth="basic",
        )
        results = (resp or {}).get("results", []) if isinstance(resp, dict) else []
        out: list[dict[str, Any]] = []
        for r in results[:max_results]:
            out.append(
                {
                    "title": str(r.get("title", ""))[:200],
                    "url": str(r.get("url", "")),
                    "content": str(r.get("content", ""))[:600],
                }
            )
        return out
    except Exception as e:
        log.warning("tavily search failed: %s", e)
        return []
