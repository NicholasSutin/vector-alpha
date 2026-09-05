#!/usr/bin/env bash
# Vector Alpha — run the IBKR Client Portal Gateway locally on port 5001.
#
#   ./scripts/ibkr-gateway.sh
#
# Downloads the gateway (once) into ./ibkr-gateway/ (git-ignored), patches the
# listen port to 5001 (macOS AirPlay Receiver squats 5000) and starts it.
# Requires Java 8+ (`java -version`).  LOG IN WITH YOUR *PAPER* USERNAME.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GW_DIR="${IBKR_GATEWAY_DIR:-$REPO_ROOT/ibkr-gateway}"
PORT="${IBKR_GATEWAY_PORT:-5001}"
ZIP_URL="https://download2.interactivebrokers.com/portal/clientportal.gw.zip"
CONF="$GW_DIR/root/conf.yaml"

command -v java >/dev/null 2>&1 || {
  echo "ERROR: java not found. Install a JDK (e.g. 'brew install --cask temurin') and retry." >&2
  exit 1
}

if [ ! -f "$GW_DIR/bin/run.sh" ]; then
  echo "==> Downloading Client Portal Gateway to $GW_DIR"
  mkdir -p "$GW_DIR"
  TMP_ZIP="$(mktemp -t clientportal.XXXXXX).zip"
  curl -fL --progress-bar "$ZIP_URL" -o "$TMP_ZIP"
  unzip -q -o "$TMP_ZIP" -d "$GW_DIR"
  rm -f "$TMP_ZIP"
  chmod +x "$GW_DIR/bin/run.sh" 2>/dev/null || true
fi

[ -f "$CONF" ] || { echo "ERROR: $CONF missing — the download looks incomplete." >&2; exit 1; }

# --- patch listenPort -> $PORT (idempotent) ---------------------------------
if grep -qE '^[[:space:]]*listenPort:' "$CONF"; then
  CURRENT_PORT="$(grep -E '^[[:space:]]*listenPort:' "$CONF" | head -1 | sed -E 's/[^0-9]*([0-9]+).*/\1/')"
  if [ "$CURRENT_PORT" != "$PORT" ]; then
    cp "$CONF" "$CONF.bak.$(date +%s)"
    sed -i.tmp -E "s/^([[:space:]]*listenPort:)[[:space:]]*[0-9]+/\1 $PORT/" "$CONF"
    rm -f "$CONF.tmp"
    echo "==> listenPort $CURRENT_PORT -> $PORT in $CONF"
  fi
else
  echo "==> WARNING: no listenPort key in conf.yaml; leaving it alone (gateway may use 5000)."
fi

# --- ensure 127.0.0.1 is allowed (leave the default list alone if unsure) ----
if grep -q "127.0.0.1" "$CONF"; then
  :
elif grep -qE '^[[:space:]]*allow:' "$CONF"; then
  sed -i.tmp -E "s/^([[:space:]]*allow:.*)/\1\n      - 127.0.0.1/" "$CONF" && rm -f "$CONF.tmp"
  echo "==> added 127.0.0.1 to ips.allow"
else
  echo "==> NOTE: could not find an ips.allow list; using the shipped default."
fi

cat <<EOF

────────────────────────────────────────────────────────────────────────────
IBKR Client Portal Gateway starting on https://localhost:$PORT
  1. Open  https://localhost:$PORT  in a browser (accept the self-signed cert).
  2. Log in with your **PAPER** IBKR username (account id starts with DU/DF).
  3. Keep this process running; Vector Alpha talks to
     https://localhost:$PORT/v1/api  (IBKR_GATEWAY_URL).
  Over SSH?  Forward the port from your laptop:
     ssh -L $PORT:localhost:$PORT <user>@<host>
  then open https://localhost:$PORT locally and log in there.
  The session idles out after ~a few minutes of no traffic — the backend sends
  /tickle keepalives while it is running.
────────────────────────────────────────────────────────────────────────────

EOF

cd "$GW_DIR"
exec bin/run.sh root/conf.yaml
