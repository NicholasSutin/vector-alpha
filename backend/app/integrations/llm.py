"""OpenAI-compatible LLM client (GIDE 'Ornith 1.0', hosted Qwen, Ollama, LM Studio ...).

Small models are assumed: no function calling, strict-JSON prompting plus a very
forgiving extractor and one retry nudge.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.config import settings

log = logging.getLogger("vector-alpha.llm")

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str | None) -> dict | None:
    """Pull the first JSON object out of a possibly-fenced / chatty model reply."""
    if not text:
        return None
    candidates: list[str] = []
    stripped = text.strip()
    candidates.append(stripped)
    for m in _FENCE_RE.finditer(text):
        candidates.append(m.group(1).strip())
    # first '{' .. last '}'
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        if not c:
            continue
        try:
            val = json.loads(c)
            if isinstance(val, dict):
                return val
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val[0]
        except Exception:
            continue
    # last resort: strip trailing commas then retry the brace slice
    if start != -1 and end > start:
        repaired = re.sub(r",\s*([}\]])", r"\1", text[start : end + 1])
        try:
            val = json.loads(repaired)
            if isinstance(val, dict):
                return val
        except Exception:
            pass
    return None


class LLMClient:
    def __init__(self) -> None:
        self._client: Any = None

    def available(self) -> bool:
        return bool(settings.has_llm)

    def _client_or_none(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.available():
            return None
        try:
            from openai import OpenAI  # type: ignore

            self._client = OpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key or "not-needed",
                timeout=settings.llm_timeout_s,
                max_retries=0,
            )
        except Exception as e:
            log.warning("openai client init failed: %s", e)
            self._client = None
        return self._client

    # ------------------------------------------------------------------
    def _call(self, messages: list[dict], max_tokens: int, temperature: float, json_mode: bool):
        client = self._client_or_none()
        kwargs: dict[str, Any] = {
            "model": settings.llm_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return client.chat.completions.create(**kwargs)

    def chat_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 1500,
        temperature: float = 0.2,
    ) -> tuple[dict | None, dict[str, Any]]:
        """-> (parsed_json_or_None, meta{model, latency_ms, tokens_in, tokens_out, raw_text, error})"""
        meta: dict[str, Any] = {
            "model": settings.llm_model or "",
            "latency_ms": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "raw_text": "",
            "error": None,
        }
        if not self.available() or self._client_or_none() is None:
            meta["error"] = "llm_unavailable"
            return None, meta

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        t0 = time.perf_counter()
        resp = None
        # GIDE's local API rejects `response_format` (and tools/n/logprobs) outright, so only
        # ask for json_object when the endpoint is known to support the richer surface.
        want_json_mode = bool(settings.llm_supports_tools)
        try:
            try:
                resp = self._call(messages, max_tokens, temperature, json_mode=want_json_mode)
            except Exception as e:
                msg = str(e)
                if want_json_mode:
                    log.info("json_object mode rejected (%s); retrying plain", msg[:120])
                    resp = self._call(messages, max_tokens, temperature, json_mode=False)
                elif "429" in msg or "rate limit" in msg.lower():
                    log.info("LLM 429 — retrying once in 3s")
                    time.sleep(3)
                    resp = self._call(messages, max_tokens, temperature, json_mode=False)
                else:
                    raise
        except Exception as e:
            # Any HTTP/connection failure => treat the LLM as unavailable; the run falls back.
            meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            meta["error"] = f"llm_unavailable: {str(e)[:300]}"
            return None, meta

        text = ""
        try:
            text = (resp.choices[0].message.content or "") if resp and resp.choices else ""
            usage = getattr(resp, "usage", None)
            if usage is not None:
                meta["tokens_in"] = int(getattr(usage, "prompt_tokens", 0) or 0)
                meta["tokens_out"] = int(getattr(usage, "completion_tokens", 0) or 0)
            if getattr(resp, "model", None):
                meta["model"] = resp.model
        except Exception as e:  # pragma: no cover
            meta["error"] = f"bad_response: {e}"
        meta["raw_text"] = text
        parsed = extract_json(text)

        if parsed is None:
            # one nudge: "return ONLY valid JSON"
            try:
                nudge = messages + [
                    {"role": "assistant", "content": text[:2000]},
                    {"role": "user", "content": "That was not valid JSON. Return ONLY valid JSON matching the schema. No prose, no code fences."},
                ]
                resp2 = self._call(nudge, max_tokens, 0.0, json_mode=False)
                text2 = (resp2.choices[0].message.content or "") if resp2.choices else ""
                meta["raw_text"] = text2 or text
                parsed = extract_json(text2)
                usage2 = getattr(resp2, "usage", None)
                if usage2 is not None:
                    meta["tokens_in"] += int(getattr(usage2, "prompt_tokens", 0) or 0)
                    meta["tokens_out"] += int(getattr(usage2, "completion_tokens", 0) or 0)
            except Exception as e:
                meta["error"] = f"json_retry_failed: {str(e)[:200]}"

        meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        if parsed is None and meta["error"] is None:
            meta["error"] = "unparseable_json"
        return parsed, meta


llm = LLMClient()
