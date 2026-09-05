# Demo — what gets shown (90 s)

Start: `./scripts/hackathon-demo.sh` (or `DEMO_RESET=1 …` for a clean take), IBKR gateway logged in on https://localhost:5001.

| # | Tab | Shown | Point made |
|---|---|---|---|
| 1 | Connect | Demo book loaded (or a Robinhood CSV dropped in), sources table, IBKR + Robinhood cards | Your own history, four input formats, paper-only broker |
| 2 | Explain the Change | Jun→Jul auto-selected (largest swing), KPI deltas, P&L bridge, ranked drivers with lot ids, behaviour flags | The variance engine finds the meaningful change and drills to transactions |
| 3 | Explain the Change | Click **Explain the change**: live agent trace (recall → compare → drill → Tavily macro/company → model), then the report: headline, what changed, why, company changes, market context, advisements | "P&L fell $2,602, 70% from five NVDA earnings-week calls, amplified by 77% more trades" — evidence-backed, sourced |
| 4 | Explain the Change | Switch to Jul→Aug, run again: prior insight review shows July's advisements **validated** | Learns across runs; builds business context in memory |
| 5 | Trade Desk | Proposed SELL NVDA → Preview (whatIf) → Execute on paper → fill, mark, P&L since fill; performance chart | Advisement becomes a real paper order; the fill feeds the next run |
| 6 | Memory & Runs | Runs list, insight memory, PRISM card (live, trace count) → open PRISM dashboard | Observe → Improve → Prove; every run is one trajectory |

Fallbacks: model busy → deterministic report (badge shows it). IBKR not logged in → Execute disabled; show the preview from a prior order instead.
