#!/usr/bin/env bash
# 60-second smoke demo against a running backend (:8000): load the demo book, compare the two
# worst/best months, run the agent, print the headline + advisements.
set -euo pipefail
API=${API:-http://localhost:8000/api}
J() { python3 -c "import sys,json; d=json.load(sys.stdin); $1"; }
echo "→ loading demo book";   curl -sS -X POST "$API/ingest/demo" | J 'print(d["inserted"], "transactions", d["date_range"])'
echo "→ periods";             curl -sS "$API/portfolio/overview" | J 'print(d["periods"])'
echo "→ variance facts (2026-06 → 2026-07)"
curl -sS "$API/periods/compare?a=2026-06&b=2026-07" | J 'print("\n".join(" • "+f for f in d["facts"]))'
echo "→ running agent"
RUN=$(curl -sS -X POST "$API/analyze" -H 'content-type: application/json' -d '{"a":"2026-06","b":"2026-07"}' | J 'print(d["run_id"])')
curl -sS -N "$API/analyze/$RUN/events" | while read -r line; do
  case "$line" in data:*) echo "${line#data: }" | python3 -c '
import sys,json
e=json.loads(sys.stdin.read())
t=e.get("type")
if t in ("status","tool_call","tool_result"): print("  ", t, e.get("name") or "", (e.get("message") or e.get("summary") or "")[:110])
elif t=="final":
    r=e["report"]; print("\nHEADLINE:", r["headline"]); print("model:", e.get("model"), "fallback:", e.get("fallback"), "prism session:", e["prism"]["session_id"])
    for a in r["advisements"]: print("  ADVISE [%s] %s — %s" % (a["priority"], a["title"], a["detail"][:120]))
    for p in r.get("proposed_trades", []): print("  TRADE  %s %s x%s %s" % (p["side"], p["symbol"], p["qty"], p["order_type"]))
elif t=="error": print("ERROR", e.get("message"))
' ;; esac
done
echo "→ PRISM"; curl -sS "$API/prism/status" | J 'print({k:d.get(k) for k in ("credential_ok","live_connected","blocked_step","live_trace_count")})'
