#!/usr/bin/env bash
# Dev mode: backend on :8000 (reload) + Vite on :5173 (proxies /api). Ctrl-C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
if [ ! -x .venv/bin/python ]; then uv venv -q -p 3.14 .venv && uv pip install -q --python .venv/bin/python -e ".[dev]"; fi
.venv/bin/uvicorn app.main:app --reload --port 8000 &
BACK=$!
cd "$ROOT/frontend"
[ -d node_modules ] || pnpm install
pnpm dev --host 0.0.0.0 &
FRONT=$!
trap 'kill $BACK $FRONT 2>/dev/null || true' EXIT INT TERM
echo ""
echo "  Vector Alpha dev:  UI http://localhost:5173   API http://localhost:8000/docs"
echo ""
wait
