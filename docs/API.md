# Vector Alpha — HTTP API contract

Base: `http://localhost:8000/api`. JSON everywhere. Errors: `{ "detail": "message" }` with 4xx/5xx.
Frontend dev server (Vite, :5173) proxies `/api` → `:8000`.

## Health / status
- `GET /health` → `{ ok: true, version, has_anthropic_key, has_tavily_key, prism: {configured, host} }`
- `GET /prism/status` → `{ configured, host, project_id, credential_ok, live_connected, blocked_step, live_trace_count, dashboard_url }` (calls PRISM setup-doctor; 10s timeout; never throws — returns `configured:false` if unset)

## Ingest
- `POST /ingest/csv` multipart: `file`, `source` ∈ `robinhood|ibkr_flex|generic` (auto-detect if omitted) → `IngestResult`
- `POST /ingest/demo` → loads the synthetic book (idempotent: clears prior `synthetic` rows) → `IngestResult`
- `POST /ingest/reset` → deletes all transactions/snapshots/runs/insights/orders → `{ ok: true }`

`IngestResult = { ok, source, account_ids: string[], inserted: number, skipped_duplicates: number, date_range: {start, end}, asset_types: {stock: n, option: n, ...}, warnings: string[] }`

## Portfolio / analytics
- `GET /portfolio/overview` → `{ has_data, sources: [{source, account_id, count, start, end}], transactions: n, periods: string[] (YYYY-MM asc), latest_period, equity_curve: [{period, realized_cum, net_deposits_cum}], positions: Position[] }`
- `GET /periods` → `{ periods: PeriodSummary[] }` (asc)
- `GET /periods/{period}` → `PeriodSummary`
- `GET /periods/compare?a=YYYY-MM&b=YYYY-MM` → `VarianceReport`
- `GET /transactions?period=YYYY-MM&symbol=&underlying=&asset_type=&limit=200` → `{ transactions: Transaction[] }`
- `GET /lots?period=YYYY-MM&underlying=&limit=200` → `{ lots: ClosedLot[] }` (lots closed in period)

```ts
type PeriodSummary = {
  period: string;                 // "2026-07"
  start: string; end: string;     // ISO dates
  realized_pnl: number; fees: number; dividends: number; interest: number;
  net_deposits: number;           // transfers in − out
  net_cash_flow: number;          // sum(amount)
  trade_count: number;            // buy/sell transactions
  closed_lots: number; wins: number; losses: number; win_rate: number;  // 0..1
  avg_win: number; avg_loss: number; expectancy: number; profit_factor: number;
  gross_bought: number; gross_sold: number; turnover: number;
  avg_hold_days: number; options_share: number;   // share of trade_count that are options 0..1
  concentration_top_symbol: string; concentration_top_share: number; // share of gross_bought
  by_underlying: Driver[]; by_asset_type: Driver[]; by_strategy: Driver[]; by_weekday: Driver[];
  largest_wins: ClosedLot[]; largest_losses: ClosedLot[];  // top 5 each
  ending_equity?: number | null; ending_cash?: number | null; // from snapshots if available
};
type Driver = { key: string; realized_pnl: number; trades: number; fees: number; gross: number; share: number };
type ClosedLot = { lot_id, symbol, underlying, asset_type, strategy_tag, open_ts, close_ts, qty, open_price, close_price, cost, proceeds, fees, realized_pnl, hold_days, open_txn_ids: string[], close_txn_ids: string[] };
type Transaction = { id, source, account_id, ts, symbol, underlying, asset_type, side, open_close, qty, price, fees, amount, description, external_id, strategy_tag };
type Position = { symbol, underlying, asset_type, qty, avg_cost, market_value?: number|null, unrealized_pnl?: number|null, account_id, source };

type VarianceReport = {
  a: PeriodSummary; b: PeriodSummary;
  topline: { metric: string; label: string; a: number; b: number; delta: number; pct: number|null; format: "usd"|"int"|"pct"|"days" }[];
  drivers: {                       // ranked by |contribution| desc, top 12
    dimension: "underlying"|"asset_type"|"strategy"|"weekday";
    key: string; a: number; b: number; delta: number;
    contribution_pct: number;      // delta / total realized_pnl delta (can exceed 1 or be negative)
    evidence_lot_ids: string[];    // lots closed in period b for this key, by |pnl|
    note: string;                  // e.g. "3 NVDA call lots opened the week before earnings"
  }[];
  bridge: { key: string; value: number }[];   // waterfall: start=a.realized_pnl, per-underlying deltas, end=b.realized_pnl
  facts: string[];                 // machine-generated evidence sentences the LLM reasons over
  behaviour_flags: string[];       // e.g. "trade_count +85%", "options_share 0.3→0.7", "avg_hold_days 9→2"
};
```

## Agent ("Explain the Change")
- `POST /analyze` body `{ a: "YYYY-MM", b: "YYYY-MM", question?: string }` → `{ run_id }` (starts background run)
- `GET /analyze/{run_id}/events` → **SSE** stream (`text/event-stream`). Each `data:` line is JSON:
  - `{ type: "status", message }`
  - `{ type: "tool_call", name, input }`
  - `{ type: "tool_result", name, summary }`   (summary ≤ 300 chars)
  - `{ type: "text", delta }`                   (optional interim model text)
  - `{ type: "final", run_id, report: Report, prism: { session_id, trajectory_url?: string|null, traced: boolean }, model, latency_ms, fallback: boolean }`
  - `{ type: "error", message }`
  The stream replays buffered events for late subscribers, then closes after `final`/`error`.
- `GET /runs` → `{ runs: RunSummary[] }` (desc)  `RunSummary = { id, created_at, period_a, period_b, status, headline, model, latency_ms, prism_session_id }`
- `GET /runs/{run_id}` → `{ run: RunSummary, report: Report|null, events: Event[] }`
- `GET /insights` → `{ insights: Insight[] }`  `Insight = { id, run_id, created_at, kind, text, evidence: string[], status }`
- `PATCH /insights/{id}` body `{ status }` → `Insight`

```ts
type Report = {
  headline: string;                        // one sentence, the "18% ... driven by ..." style
  what_changed: string[];                  // evidence-backed bullets
  why: string[];
  drivers: { name: string; contribution_pct: number; detail: string; evidence: string[] }[];   // evidence = lot ids / txn ids / urls
  behaviour: string[];                     // trading-behaviour observations (overtrading, hold time, concentration)
  advisements: { title: string; detail: string; priority: "high"|"medium"|"low"; evidence: string[] }[];
  proposed_trades: ProposedTrade[];
  prior_insight_review: { insight_id: string; text: string; verdict: "followed"|"ignored"|"validated"|"invalidated"|"unclear"; note: string }[];
  confidence: number;                      // 0..1
  sources: string[];                       // urls from web_search
};
type ProposedTrade = { id: string; symbol: string; side: "BUY"|"SELL"; qty: number; order_type: "MKT"|"LMT"; limit_price?: number|null; tif: "DAY"|"GTC"; rationale: string };
```

## Brokers
### IBKR (paper only — server refuses any account id not starting DU/DF for order endpoints)
- `GET /brokers/ibkr/status` → `{ configured, gateway_url, reachable, authenticated, connected, accounts: string[], selected_account: string|null, paper: boolean, login_url }`
- `POST /brokers/ibkr/select_account` `{ account_id }` → status
- `POST /brokers/ibkr/sync` → pulls positions + summary + recent trades (`days` ≤ 7 by API limit) into `snapshots` + `transactions` (source `ibkr_live`) → `IngestResult & { positions: Position[] }`
- `POST /brokers/ibkr/flex` `{ token, query_id }` → fetches Flex statement (poll until ready, ≤60s) → `IngestResult` (source `ibkr_flex`)
- `POST /brokers/ibkr/quote` `{ symbol }` → `{ symbol, conid, last, bid, ask }`
- `POST /brokers/ibkr/orders/preview` `{ symbol, side, qty, order_type, limit_price?, tif?, run_id?, rationale? }` → `{ order_id (ours), conid, preview: {...whatif}, warnings: string[] }`
- `POST /brokers/ibkr/orders/place` `{ order_id }` (must be previewed) → `{ order_id, status, broker_order_id, messages: string[] }` (auto-confirms IBKR "reply" prompts up to 3 times)
- `GET /brokers/ibkr/orders` → `{ orders: Order[] }` (ours, desc) + refreshes status from gateway when possible
- `DELETE /brokers/ibkr/orders/{order_id}` → cancel

### Robinhood
- `POST /brokers/robinhood/login` `{ username, password, mfa_code? }` → `{ status: "ok"|"challenge"|"error", message, challenge_type? }`. A `challenge` means the user must approve in the Robinhood app / enter SMS code; the client then calls again with `mfa_code` or polls `/brokers/robinhood/status`.
- `GET /brokers/robinhood/status` → `{ logged_in, username?: string, message }`
- `POST /brokers/robinhood/sync` → orders (stock/option/crypto) + dividends + transfers → transactions (source `robinhood_live`) → `IngestResult`
- `POST /brokers/robinhood/logout`

## SSE conventions
`Content-Type: text/event-stream`, `Cache-Control: no-cache`, lines `data: {json}\n\n`, keepalive comment `: ping\n\n` every 15s.

## Addendum (13:40) — market/macro context
`Report` gains an optional `market_context: string[]` (default `[]`): 3-6 bullets describing the market/macro regime in period B vs A (index moves, VIX, Fed/rates, sector events) sourced via Tavily, each ending with a `[source: url]` tag where possible. The frontend renders it as a "Market context" section when non-empty.

## Addendum (13:45) — Trade Desk: execution outcomes + performance

### `GET /performance` (analytics) → portfolio performance for the whole book
```ts
{
  monthly: { period: string; realized_pnl: number; cum_realized: number; net_deposits: number; cum_net_deposits: number;
             fees: number; trade_count: number; win_rate: number }[];   // asc
  stats: { total_realized: number; total_fees: number; total_net_deposits: number; months: number;
           best_period: string|null; worst_period: string|null; max_drawdown: number;    // on cum_realized
           win_rate: number; profit_factor: number; expectancy: number; avg_hold_days: number };
  by_underlying: Driver[];        // whole-book, sorted by |realized_pnl| desc, top 15
  positions: Position[];          // current open positions (replay + latest snapshot)
}
```

### Orders (brokers) — enriched `Order` shape returned by `GET /brokers/ibkr/orders`, preview, place
```ts
type Order = {
  id: string; created_at: string; run_id: string|null; account_id: string; symbol: string; conid: number|null;
  side: "BUY"|"SELL"; qty: number; order_type: "MKT"|"LMT"; limit_price: number|null; tif: string;
  status: "proposed"|"previewed"|"submitted"|"filled"|"partially_filled"|"cancelled"|"rejected"|"error";
  broker_order_id: string|null; rationale: string;
  preview: { commission?: number|null; equity_with_loan_before?: number|null; equity_with_loan_after?: number|null; amount?: string|null; warnings: string[] } | null;
  fill: { price: number|null; qty: number|null; time: string|null } | null;     // from gateway order status / trades
  mark: number|null;                 // latest snapshot last price (best effort)
  pnl_since_fill: number|null;       // (mark − fill.price) × qty × (BUY:+1 / SELL:−1)
  messages: string[];                // broker confirmation / rejection messages
};
```
`GET /brokers/ibkr/orders` refreshes status/fill/mark from the gateway when reachable (never throws; stale values otherwise).
When an order fills, the broker module also inserts the fill as a `transactions` row (source `ibkr_live`, external_id = broker_order_id) so the next agent run sees it.

## Addendum (14:02) — company changes + fast LLM layer
`Report.company_changes: { symbol, text, sources[] }[]` (default `[]`): for each top-driver underlying, what changed at the
company during period B (earnings, guidance, product/news, price move) from Tavily, summarised by the model when available.
These are also saved as insights with `kind: "context"` so later runs recall business context ("build intuition over runs").
The LLM step now produces only a compact narrative layer (`headline`, `why`, `market_context`, `company_changes[].text`,
`behaviour`) merged over the deterministic report (facts, drivers, advisements, trades, prior review stay engine-computed).
