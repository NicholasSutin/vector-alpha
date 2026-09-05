"""Environment-driven settings. Import `settings` anywhere.

All values are optional so the app boots with nothing configured; features degrade:
- no LLM endpoint  -> deterministic narrative fallback (still PRISM-traced)
- no Tavily key    -> web_search step skipped
- no PRISM key     -> tracing disabled (never blocks the app)
- no IBKR gateway  -> broker endpoints report `reachable: false`
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]          # repo root
BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"

# Load repo-root .env first, then backend/.env (later wins), never overriding real env.
for candidate in (ROOT / ".env", BACKEND_DIR / ".env"):
    if candidate.exists():
        load_dotenv(candidate, override=False)


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- app ---
    db_path: Path = field(default_factory=lambda: Path(os.getenv("VA_DB_PATH", str(DATA_DIR / "vector_alpha.db"))))
    frontend_dist: Path = field(default_factory=lambda: ROOT / "frontend" / "dist")
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000")

    # --- LLM (OpenAI-compatible: GIDE local API, Ollama, LM Studio, hosted Qwen ...) ---
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")            # e.g. http://localhost:1234/v1
    llm_api_key: str = os.getenv("LLM_API_KEY", "not-needed")
    llm_model: str = os.getenv("LLM_MODEL", "")                  # e.g. ornith-1.0
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_S", "120"))
    llm_supports_tools: bool = _bool(os.getenv("LLM_SUPPORTS_TOOLS"), False)
    # optional second provider tried when the primary errors or returns unusable JSON
    llm_fallback_base_url: str = os.getenv("LLM_FALLBACK_BASE_URL", "")
    llm_fallback_model: str = os.getenv("LLM_FALLBACK_MODEL", "")
    llm_fallback_api_key: str = os.getenv("LLM_FALLBACK_API_KEY", "")

    # --- Tavily ---
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")

    # --- PRISM (Block Convey) ---
    prism_api_key: str = os.getenv("PRISMTRACE_API_KEY", "")
    prism_project_id: str = os.getenv("PRISMTRACE_PROJECT_ID", "")
    prism_host: str = os.getenv("PRISMTRACE_HOST", "https://prism-api-prod.up.railway.app")
    prism_agent_name: str = os.getenv("PRISM_AGENT_NAME", "vector-alpha-explain-the-change")
    prism_agent_id: str = os.getenv("PRISM_AGENT_ID", "vector-alpha-analyst")

    # --- IBKR (Client Portal Gateway, paper only) ---
    ibkr_gateway_url: str = os.getenv("IBKR_GATEWAY_URL", "https://localhost:5001/v1/api")
    ibkr_account_id: str = os.getenv("IBKR_ACCOUNT_ID", "")
    ibkr_paper_only: bool = _bool(os.getenv("IBKR_PAPER_ONLY"), True)   # never set false in this repo
    ibkr_flex_token: str = os.getenv("IBKR_FLEX_TOKEN", "")
    ibkr_flex_query_id: str = os.getenv("IBKR_FLEX_QUERY_ID", "")

    # --- Robinhood ---
    rh_session_dir: Path = field(default_factory=lambda: Path(os.getenv("RH_SESSION_DIR", str(Path.home() / ".tokens" / "vector-alpha"))))

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    @property
    def has_llm_fallback(self) -> bool:
        return bool(self.llm_fallback_base_url and self.llm_fallback_model)

    @property
    def has_tavily(self) -> bool:
        return bool(self.tavily_api_key)

    @property
    def has_prism(self) -> bool:
        return bool(self.prism_api_key and self.prism_project_id)


settings = Settings()
