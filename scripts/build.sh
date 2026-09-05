#!/usr/bin/env bash
# Production-ish: build the frontend, then serve everything from FastAPI on :8000.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend" && ([ -d node_modules ] || pnpm install) && pnpm build
cd "$ROOT/backend"
if [ ! -x .venv/bin/python ]; then uv venv -q -p 3.14 .venv && uv pip install -q --python .venv/bin/python -e ".[dev]"; fi
echo "  Serving UI + API at http://localhost:8000"
exec .venv/bin/uvicorn app.main:app --port 8000 --host 0.0.0.0
