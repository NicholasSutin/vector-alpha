"""Test env: isolated sqlite db + no network (PRISM / LLM / Tavily all disabled).

`load_dotenv` happily overwrites an env var that is set to "", so clearing the environment is
not enough to keep the real repo `.env` out of a test run. Every module does
`from app.config import settings`, i.e. they all share ONE frozen Settings instance — so we
blank the credentials on that instance in place, which every importer sees.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import fields
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="vector-alpha-test-"))
os.environ["VA_DB_PATH"] = str(_TMP / "test.db")
for _k in ("PRISMTRACE_API_KEY", "PRISMTRACE_PROJECT_ID", "TAVILY_API_KEY",
           "LLM_BASE_URL", "LLM_MODEL", "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_MODEL", "LLM_FALLBACK_API_KEY"):
    os.environ[_k] = ""

import pytest  # noqa: E402

from app.config import settings  # noqa: E402

# hard-disable every outbound integration for the whole test session
for _field, _blank in (
    ("llm_base_url", ""), ("llm_model", ""), ("tavily_api_key", ""),
    ("llm_fallback_base_url", ""), ("llm_fallback_model", ""), ("llm_fallback_api_key", ""),
    ("prism_api_key", ""), ("prism_project_id", ""),
    ("db_path", _TMP / "test.db"),
):
    object.__setattr__(settings, _field, _blank)   # frozen dataclass, shared instance

assert not settings.has_llm and not settings.has_tavily and not settings.has_prism
assert str(settings.db_path).startswith(str(_TMP))

from app.db import init_db, reset_all  # noqa: E402


_SNAPSHOT = {f.name: getattr(settings, f.name) for f in fields(settings)}


@pytest.fixture(autouse=True)
def _pristine_settings():
    """Settings is a frozen, shared singleton, so a test that enables an integration with
    `object.__setattr__` would leak it into every later test. Restore it after each test."""
    yield
    for _name, _value in _SNAPSHOT.items():
        if getattr(settings, _name) != _value:
            object.__setattr__(settings, _name, _value)


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    reset_all()
    yield


@pytest.fixture(autouse=True)
def _no_prism(monkeypatch):
    """Belt and braces: even if a key were present, the client stays disabled."""
    from app.integrations import prism as prism_mod

    monkeypatch.setattr(prism_mod.prism, "enabled", False, raising=False)
    monkeypatch.setattr(prism_mod.prism, "_client", None, raising=False)
    yield
