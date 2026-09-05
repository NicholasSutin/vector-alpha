# Demo script (≤ 90 seconds)

Setup before recording: `./scripts/hackathon-demo.sh` (or `cd frontend && pnpm run hackathon-demo`).
Have the IBKR paper gateway logged in (https://localhost:5001) if you want to show a real fill.

| t | Screen | Say |
|---|---|---|
| 0:00 | Explain the Change tab, June → July selected | "Every trader gets a red number and no explanation. Vector Alpha is the Money-Ops 'explain the change' agent, pointed at your own brokerage history." |
| 0:10 | KPI tiles + P&L bridge | "Connect Robinhood or IBKR history, or the demo book. July P&L fell $2,600 versus June. The bridge already shows NVDA, AMD and TSLA options did it." |
| 0:20 | Click **Explain the change**; agent trace streams | "The agent recalls last month's insights, compares the periods, drills into the lots behind each driver, pulls market context from Tavily, and reasons on a local model through GIDE. Every step is traced to PRISM." |
| 0:40 | Report: headline, drivers, behaviour | "Not 'P&L fell 145%' but: 70% of the decline was five NVDA earnings-week calls, amplified by 73% more trades, hold time collapsing from 9 days to 2, and NVDA becoming 55% of what you bought." |
| 0:55 | Advisements + prior insight review | "It turns that into measurable rules and, next month, checks whether you followed them. Memory across runs." |
| 1:05 | Trade Desk: Preview → Execute on IBKR paper | "It can act: the concentration advisement becomes a paper order on IBKR, previewed with whatIf, then filled. The fill lands back in the ledger for the next run." |
| 1:20 | Memory & Runs: PRISM card (live connected, trace count) | "Observe, improve, prove: PRISM shows the trajectory of every run. Built today, from scratch, on GIDE. Thanks." |

Fallbacks: if the local model is busy, the run completes in deterministic mode (same report shape, badge shows it).
If IBKR is not logged in, Execute buttons are disabled with a tooltip — show the preview JSON from a prior run instead.
