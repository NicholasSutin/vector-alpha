#!/usr/bin/env bash
# Package Vector Alpha for another laptop: repo + .env (KEYS INCLUDED) + seeded DB + built UI +
# IBKR gateway + git bundle + handoff notes.   Usage: ./scripts/pack-usb.sh [/Volumes/usb-32gb]
# Add INCLUDE_GIDE_MODEL=1 to also copy the 6.2 GB local model (only useful on Apple Silicon).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-/Volumes/usb-32gb}/vector-alpha"
STAMP="$(date +%Y-%m-%d_%H%M)"
[ -d "$(dirname "$DEST")" ] || { echo "destination $(dirname "$DEST") not mounted" >&2; exit 1; }
mkdir -p "$DEST"
echo "▸ packing to $DEST"

# 1. git bundle (full history, all refs)
git -C "$ROOT" bundle create "$DEST/vector-alpha.bundle" --all >/dev/null 2>&1 && echo "  ✔ git bundle"

# 2. working tree tarball — includes .env (secrets), backend/data/*.db, frontend/dist, ibkr-gateway (patched conf)
tar -C "$(dirname "$ROOT")" -czf "$DEST/vector-alpha-$STAMP.tar.gz" \
  --exclude='vector-alpha/backend/.venv' --exclude='vector-alpha/frontend/node_modules' \
  --exclude='vector-alpha/.demo-logs' --exclude='vector-alpha/.git' --exclude='*/__pycache__' \
  --exclude='vector-alpha/backend/data/*.db-wal' --exclude='vector-alpha/backend/data/*.db-shm' \
  --exclude='vector-alpha/backend/*.egg-info' \
  vector-alpha && echo "  ✔ tree tarball (with .env, db, dist, ibkr-gateway)"

# 3. loose copies of the things people look for first
cp "$ROOT/.env" "$DEST/dot-env.txt"
cp "$ROOT/README.md" "$DEST/README.md"
cp "$ROOT/docs/DEMO.md" "$DEST/DEMO.md"
mkdir -p "$DEST/screenshots" && cp "$ROOT"/docs/screenshot-*.png "$DEST/screenshots/" 2>/dev/null || true

# 4. optional local model
if [ "${INCLUDE_GIDE_MODEL:-0}" = "1" ] && [ -d "$HOME/.gide/models" ]; then
  mkdir -p "$DEST/gide-model" && cp "$HOME"/.gide/models/*.gguf "$DEST/gide-model/" && echo "  ✔ GIDE model copied"
fi

cat > "$DEST/HANDOFF.md" <<HANDOFF
# Vector Alpha — laptop handoff ($STAMP)

Everything needed to run the demo on another Mac/Linux laptop. **Keys are inside** (\`dot-env.txt\` = the repo's \`.env\`):
Cloudflare Workers AI (primary LLM), OpenRouter (fallback), Tavily, PRISM, GIDE. Do not commit it anywhere.

## Fastest path (needs: Python 3.12+, \`uv\`, Java for IBKR; internet for pip)
\`\`\`bash
tar -xzf vector-alpha-$STAMP.tar.gz && cd vector-alpha      # .env, seeded DB and built UI are already inside
./scripts/hackathon-demo.sh                                 # one command: API+UI on :8000, IBKR gateway on :5001, opens tabs
\`\`\`
The first run creates backend/.venv and installs deps (~1 min). Frontend is pre-built (frontend/dist); Node is not required.
For hot-reload dev: \`./scripts/dev.sh\` (needs Node 20+ and pnpm).

## What is already seeded
- \`backend/data/vector_alpha.db\`: the synthetic Jan–Aug 2026 book, two completed runs (Jun→Jul, Jul→Aug) and the insight memory.
  \`DEMO_RESET=1 ./scripts/hackathon-demo.sh\` wipes runs/insights and reloads the book.
- \`ibkr-gateway/\`: IBKR Client Portal Gateway patched to port 5001. Log in at https://localhost:5001 with the PAPER username (DU…);
  accept the self-signed-certificate warning (Advanced → proceed, or type \`thisisunsafe\`).
- Git history: \`git clone vector-alpha.bundle vector-alpha-src\` (or use GitHub: https://github.com/NicholasSutin/vector-alpha).

## Optional: run the model offline with GIDE (Apple Silicon, 16 GB+)
\`curl -fsSL https://generativeide.com/install.sh | sh\`, \`gide models pull ornith-1.5-9b\` (or copy gide-model/*.gguf into ~/.gide/models),
\`gide server start && gide apikey create vector-alpha\`, then in .env set LLM_BASE_URL=http://127.0.0.1:41337/v1, LLM_MODEL=local, LLM_API_KEY=sk-gide-…

See DEMO.md for the 90-second walkthrough and README.md for everything else.
HANDOFF
echo "  ✔ HANDOFF.md"
( cd "$DEST" && shasum -a 256 vector-alpha.bundle vector-alpha-$STAMP.tar.gz > SHA256SUMS.txt )
echo; du -sh "$DEST"; ls -la "$DEST"
