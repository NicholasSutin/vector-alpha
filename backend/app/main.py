"""FastAPI entrypoint. Run: `uv run uvicorn app.main:app --reload --port 8000` from backend/."""
from __future__ import annotations

import importlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import settings
from app.db import init_db

log = logging.getLogger("vector-alpha")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Vector Alpha", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "has_llm": settings.has_llm,
        "llm_model": settings.llm_model or None,
        "has_tavily": settings.has_tavily,
        "prism": {"configured": settings.has_prism, "host": settings.prism_host},
    }


# Routers are optional so the app boots while modules are being built.
for mod_name in ("app.routes.ingest", "app.routes.analytics", "app.routes.agent", "app.routes.brokers"):
    try:
        mod = importlib.import_module(mod_name)
        app.include_router(mod.router, prefix="/api")
        log.info("mounted %s", mod_name)
    except ModuleNotFoundError as e:  # module not written yet
        if e.name and e.name.startswith("app.routes"):
            log.warning("router %s not present yet", mod_name)
        else:
            raise
    except AttributeError:
        log.warning("router %s has no `router`", mod_name)


# Serve the built frontend (frontend/dist) at / when present; SPA fallback to index.html.
_dist = settings.frontend_dist
if _dist.exists() and (_dist / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        target = _dist / full_path
        if full_path and target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(_dist / "index.html"))
