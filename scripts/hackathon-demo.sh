#!/usr/bin/env bash
# ONE-COMMAND DEMO.  ./scripts/hackathon-demo.sh   (or: cd frontend && pnpm run hackathon-demo)
#
# Starts everything needed for the presentation and opens the relevant tabs:
#   1. GIDE local model server (the LLM; no cloud keys)            [if `gide` is installed]
#   2. IBKR Client Portal Gateway on :5001 (paper login page)      [if DEMO_IBKR=1 or ./ibkr-gateway exists]
#   3. Backend API + built UI on http://localhost:8000
#   4. Loads the synthetic demo book if the DB is empty
#   5. Opens tabs: app · IBKR login · PRISM dashboard (· Robinhood reports with DEMO_OPEN_ROBINHOOD=1)
#
# Env knobs: DEMO_PORT=8000  DEMO_DEV=1 (Vite dev server instead of build)  DEMO_NO_BROWSER=1
#            DEMO_IBKR=0|1   DEMO_RELOAD=1 (reload demo book)  DEMO_OPEN_ROBINHOOD=1
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"
PORT="${DEMO_PORT:-8000}"
API="http://localhost:$PORT/api"
LOGS="$ROOT/.demo-logs"; mkdir -p "$LOGS"
PIDS=()
say()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
cleanup() { echo; say "stopping…"; for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; wait 2>/dev/null; }
trap cleanup EXIT INT TERM

cd "$ROOT"
[ -f .env ] || { cp .env.example .env; warn "created .env from .env.example (fill LLM_*/TAVILY_API_KEY/PRISMTRACE_* as available)"; }

# ---------- 1. GIDE local LLM ----------
if command -v gide >/dev/null 2>&1; then
  gide server start >/dev/null 2>&1 || true
  if gide doctor 2>/dev/null | grep -q "✔ local model installed"; then
    ok "GIDE server up with a local model ($(gide server status 2>/dev/null | head -1))"
  else
    warn "GIDE server up but no local model yet (gide models pull ornith-1.5-9b). Agent runs in deterministic mode."
  fi
  if ! grep -qE '^LLM_BASE_URL=.+' .env; then
    warn "LLM_BASE_URL not in .env — run: gide apikey create vector-alpha  and add LLM_BASE_URL / LLM_MODEL=local / LLM_API_KEY"
  fi
else
  warn "gide CLI not installed (curl -fsSL https://generativeide.com/install.sh | sh). Agent runs in deterministic mode."
fi

# ---------- 2. IBKR gateway (paper) ----------
IBKR_STARTED=0
if [ "${DEMO_IBKR:-auto}" != "0" ] && { [ "${DEMO_IBKR:-auto}" = "1" ] || [ -d "$ROOT/ibkr-gateway" ]; }; then
  if [ -x "$ROOT/scripts/ibkr-gateway.sh" ]; then
    say "starting IBKR Client Portal Gateway on :5001 (log: .demo-logs/ibkr.log)"
    ( "$ROOT/scripts/ibkr-gateway.sh" >"$LOGS/ibkr.log" 2>&1 ) & PIDS+=($!)
    IBKR_STARTED=1
  fi
fi

# ---------- 3. backend (+ UI) ----------
cd "$ROOT/backend"
if [ ! -x .venv/bin/python ]; then say "creating venv"; uv venv -q -p 3.14 .venv && uv pip install -q --python .venv/bin/python -e ".[dev]"; fi
cd "$ROOT/frontend"
[ -d node_modules ] || { say "pnpm install"; pnpm install --silent; }
if [ "${DEMO_DEV:-0}" = "1" ]; then
  ( pnpm dev --host 0.0.0.0 >"$LOGS/vite.log" 2>&1 ) & PIDS+=($!)
  UI_URL="http://localhost:5173"
else
  say "building UI"; pnpm build >"$LOGS/build.log" 2>&1 || { warn "UI build failed — see .demo-logs/build.log"; }
  UI_URL="http://localhost:$PORT"
fi
cd "$ROOT/backend"
say "starting API on :$PORT (log: .demo-logs/api.log)"
( .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$PORT" >"$LOGS/api.log" 2>&1 ) & PIDS+=($!)
for i in $(seq 1 40); do curl -sf "$API/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -sf "$API/health" >/dev/null 2>&1 && ok "API healthy: $(curl -s "$API/health")" || { warn "API did not come up — see .demo-logs/api.log"; }

# ---------- 4. demo data ----------
HAS=$(curl -s "$API/portfolio/overview" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("has_data",False))' 2>/dev/null || echo False)
if [ "$HAS" != "True" ] || [ "${DEMO_RELOAD:-0}" = "1" ]; then
  R=$(curl -s -X POST "$API/ingest/demo"); ok "demo book loaded: $(echo "$R" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("inserted"),"txns",d.get("date_range"))' 2>/dev/null)"
else ok "data already present (DEMO_RELOAD=1 to reload the demo book)"; fi

# ---------- 5. tabs ----------
PRISM_URL="https://prism.blockconvey.com"
TABS=("$UI_URL")
[ "$IBKR_STARTED" = "1" ] && TABS+=("https://localhost:5001")
TABS+=("$PRISM_URL")
[ "${DEMO_OPEN_ROBINHOOD:-0}" = "1" ] && TABS+=("https://robinhood.com/account/reports-statements")
if [ "${DEMO_NO_BROWSER:-0}" != "1" ] && [ -z "${SSH_CONNECTION:-}" ] && command -v open >/dev/null 2>&1; then
  [ "$IBKR_STARTED" = "1" ] && sleep 6   # give the gateway a moment before opening its login page
  for t in "${TABS[@]}"; do open "$t"; done
fi

echo; printf '\033[1m  VECTOR ALPHA — demo is up\033[0m\n'
for t in "${TABS[@]}"; do echo "   • $t"; done
echo "   • API docs  http://localhost:$PORT/docs"
echo "   • CLI demo  ./scripts/demo.sh"
echo "   • remote dev: ssh -L $PORT:localhost:$PORT -L 5001:localhost:5001 $(whoami)@$(hostname) ; then open the URLs above locally"
echo "   Ctrl-C stops everything."; echo
wait
