# Vector Alpha — Architecture

**One-liner:** Connect your own brokerage history (Robinhood, IBKR), and an agent explains
*what changed, why it changed, and what is driving it* across periods — then turns that into
advisements and (optionally) paper trades on IBKR.

Built for the MONEY TALKS hackathon, **Money Operations track (Maximor: "Explain the Change")**.
The track asks for an agent that takes monthly account summaries + transaction-level data,
compares periods, ranks the meaningful variances, drills into transactions for drivers, and
produces an evidence-backed explanation — and that *learns across runs*.

We apply that to a personal trading book:
- "monthly account summary" = our computed `PeriodSummary` (realized P&L, fees, dividends,
  deposits, trade count, win rate, concentration ...)
- "transaction-level CSV" = normalized broker transactions (Robinhood activity CSV, IBKR Flex
  query CSV, live IBKR/Robinhood pulls, or the synthetic demo book)
- "learn from previous runs" = an `insights` memory the agent reads at the start of every run
  and reviews ("last month I advised X — was it followed, did it help?")

## Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.14, FastAPI, uvicorn, pandas, sqlite3 | analytics + robin_stocks + prismtrace are Python |
| LLM | Anthropic Messages API, tool use loop | traced by `prismtrace.ClaudeAgentTracer` (PRISM) |
| Observability | **PRISM** (prismtrace-sdk 0.4.x) | required by hackathon; every run = one trajectory |
| Web research | Tavily (`tavily-python`) | "why did NVDA drop in July?" evidence |
| Frontend | Vite + React + TypeScript + Tailwind + recharts | fast, demo-friendly |
| Brokers | IBKR Client Portal Web API (local gateway) + Flex Web Service; robin_stocks | paper trading + history |

## Module ownership (each built independently against `docs/API.md`)

```
backend/app/
  config.py        env → Settings (done)
  db.py            sqlite connection + schema + tiny repo helpers (done)
  models.py        shared dataclasses / pydantic models (done)
  main.py          FastAPI app, CORS, router mounting, static frontend (done)
  ingest/          [analytics-core agent]  robinhood_csv.py, ibkr_flex_csv.py, generic_csv.py, synthetic.py, normalize.py
  analytics/       [analytics-core agent]  ledger.py (FIFO lots), periods.py (PeriodSummary), variance.py (compare), positions.py
  routes/analytics.py, routes/ingest.py   [analytics-core agent]
  agent/           [agent-runner agent]    tools.py, runner.py (Claude loop + PRISM), memory.py, prompts.py, fallback.py, events.py
  routes/agent.py                          [agent-runner agent]
  integrations/prism.py, tavily.py         [agent-runner agent]
  brokers/ibkr.py, ibkr_flex.py, robinhood.py, guard.py   [brokers agent]
  routes/brokers.py                        [brokers agent]
frontend/                                  [frontend agent]
```

## Data model (sqlite, `backend/data/vector_alpha.db`)

`transactions` — one row per broker event (normalized across sources)

| col | type | meaning |
|---|---|---|
| id | TEXT PK | uuid4 or `source:external_id` |
| source | TEXT | `synthetic` / `robinhood_csv` / `robinhood_live` / `ibkr_flex` / `ibkr_live` / `generic_csv` |
| account_id | TEXT | broker account id (paper IBKR ids start with `DU`/`DF`) |
| ts | TEXT | ISO 8601 datetime (UTC ok) — trade/activity date |
| symbol | TEXT | instrument symbol. Options use `UNDERLYING YYYY-MM-DD C|P STRIKE` e.g. `NVDA 2026-06-20 C 140` |
| underlying | TEXT | ticker (same as symbol for stocks) |
| asset_type | TEXT | `stock` / `option` / `crypto` / `dividend` / `interest` / `fee` / `transfer` / `other` |
| side | TEXT | `buy` / `sell` / `none` |
| open_close | TEXT | `open` / `close` / `` (options: BTO/STO=open, BTC/STC=close; expirations/assignments = close) |
| qty | REAL | contracts for options (1 contract = 100 multiplier applied in `amount`) |
| price | REAL | per-unit price |
| fees | REAL | commissions + regulatory fees, positive number |
| amount | REAL | **signed cash flow** to the account: buys negative, sells positive, deposits positive, fees negative |
| description | TEXT | raw description from source |
| external_id | TEXT | broker order/trade id if any |
| strategy_tag | TEXT | optional label (`earnings`, `swing`, `income`...) — synthetic data sets these |
| raw_json | TEXT | original row for evidence |

`snapshots(id, source, account_id, as_of, equity, cash, positions_json)` — optional point-in-time equity (from live pulls).

`runs(id, created_at, period_a, period_b, question, status, report_json, prism_session_id, model, latency_ms, error)`

`insights(id, run_id, created_at, kind, text, evidence_json, status)` — memory across runs. kind ∈ `observation|advisement|prediction`; status ∈ `open|followed|ignored|validated|invalidated`.

`orders(id, created_at, run_id, account_id, symbol, conid, side, qty, order_type, limit_price, tif, status, broker_order_id, preview_json, result_json, rationale)`

`settings(key TEXT PK, value TEXT)` — kv (e.g. ibkr account id, last sync).

## Analytics contracts (pure functions, pandas in, dicts out — see `models.py`)

- `ledger.match_lots(txns) -> list[ClosedLot]` — FIFO per `symbol` (stock and per option contract separately). Each closed lot: `lot_id, symbol, underlying, asset_type, strategy_tag, open_ts, close_ts, qty, open_price, close_price, cost, proceeds, fees, realized_pnl, hold_days, open_txn_ids, close_txn_ids`. Expirations (OEXP) close at price 0; assignments close at strike (treat as close at 0 premium + stock leg if present; keep simple).
- `periods.list_periods(txns) -> list[str]` `YYYY-MM` months present.
- `periods.summarize(period, txns, lots) -> PeriodSummary` (see models.py for fields).
- `variance.compare(a: PeriodSummary, b: PeriodSummary, lots) -> VarianceReport` — topline deltas, ranked drivers with **contribution %** and evidence lot ids, bridge (waterfall by symbol), and short machine-generated `facts` sentences ("Realized P&L fell $2,140 (−63%); NVDA options contributed 71% of the decline (3 lots)").

The **facts** list is what the LLM reasons over and what the deterministic fallback narrates.

## Agent (Money-Ops "explain the change")

Tool loop: Claude with tools that call the analytics functions (never raw SQL from the model):
`list_periods`, `get_period_summary`, `compare_periods`, `drill_down`, `get_transactions`,
`get_positions`, `web_search` (Tavily), `recall_insights`, `save_insight`, `propose_trade`,
`submit_report` (terminal: structured report).

Every run: `session_id = run_id`; traced via `prismtrace.ClaudeAgentTracer(anthropic_client, ...)`
so PRISM shows the trajectory (model calls + tool calls + latency). If `ANTHROPIC_API_KEY` is
missing, `fallback.py` builds the same report shape from `VarianceReport.facts` so the UI and
demo still work, and a `trace_llm` is still emitted with model `fallback` (marked in metadata).

Report shape → `models.Report` (headline, what_changed[], why[], drivers[], advisements[],
proposed_trades[], prior_insight_review[], confidence, evidence[]).

## Brokers

- **IBKR** (paper only): local Client Portal Gateway at `IBKR_GATEWAY_URL` (default
  `https://localhost:5001/v1/api`, self-signed → verify=False). Endpoints: auth/status, tickle,
  portfolio/accounts, positions, summary, ledger, trades (days), secdef/search, marketdata/snapshot,
  orders/whatif, orders (POST), reply/{id}, orders list, cancel. **Guard:** any order call with an
  account id not starting `DU`/`DF` raises `PaperOnlyViolation`. Longer history via Flex Web
  Service (token + query id → CSV → `ingest/ibkr_flex_csv.py`).
- **Robinhood**: robin_stocks login (username/password/TOTP or device approval) from a UI form;
  sync stock/option/crypto orders + dividends + transfers → transactions. CSV activity export is
  the primary path (no login needed).

## PRISM tracing (do not remove)

This project sends traces to PRISM. Env vars: `PRISMTRACE_API_KEY`,
`PRISMTRACE_PROJECT_ID`, `PRISMTRACE_HOST`.

Tracing is currently wired at: `backend/app/agent/runner.py` (ClaudeAgentTracer around the
analysis loop), `backend/app/agent/fallback.py` (trace_llm), `backend/app/integrations/prism.py`.

**Standing rule.** Whenever you add or change an agent, chain, graph, tool,
retriever, or any entry point that calls a model, wire it to PRISM before you
finish. Unwired code is invisible in the dashboard. If you are unsure whether
something is covered, assume it is not and wire it.
