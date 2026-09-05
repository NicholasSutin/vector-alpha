"""Point the app at a throwaway sqlite file BEFORE app.config is ever imported.

`app.config.settings` is a frozen dataclass built at import time, so the env var
has to be set before the first `import app.config` anywhere in the process.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DB = Path(tempfile.mkdtemp(prefix="vector-alpha-tests-")) / "test.db"
os.environ["VA_DB_PATH"] = str(_TMP_DB)

import pytest  # noqa: E402

from app.db import init_db, reset_all  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _db() -> None:
    init_db()
    yield
    try:
        _TMP_DB.unlink(missing_ok=True)
    except OSError:
        pass


@pytest.fixture()
def clean_db() -> None:
    """Empty database for a single test."""
    from app.analytics import service
    init_db()
    reset_all()
    service.invalidate_cache()
    yield
    reset_all()
    service.invalidate_cache()
