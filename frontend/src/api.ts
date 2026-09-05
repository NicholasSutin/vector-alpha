// Typed client for the Vector Alpha backend. Types mirror docs/API.md.

export type Health = {
  ok: boolean;
  version: string;
  has_llm?: boolean;
  llm_model?: string | null;
  has_anthropic_key?: boolean;
  has_tavily_key?: boolean;
  has_tavily?: boolean;
  model?: string | null;
  prism: { configured: boolean; host: string };
};

export type PrismStatus = {
  configured: boolean;
  host: string;
  project_id: string | null;
  credential_ok: boolean;
  live_connected: boolean;
  blocked_step: string | null;
  live_trace_count: number;
  dashboard_url: string | null;
};

export type IngestResult = {
  ok: boolean;
  source: string;
  account_ids: string[];
  inserted: number;
  skipped_duplicates: number;
  date_range: { start: string | null; end: string | null };
  asset_types: Record<string, number>;
  warnings: string[];
};

export type Driver = {
  key: string;
  realized_pnl: number;
  trades: number;
  fees: number;
  gross: number;
  share: number;
};

export type ClosedLot = {
  lot_id: string;
  symbol: string;
  underlying: string;
  asset_type: string;
  strategy_tag: string;
  open_ts: string;
  close_ts: string;
  qty: number;
  open_price: number;
  close_price: number;
  cost: number;
  proceeds: number;
  fees: number;
  realized_pnl: number;
  hold_days: number;
  open_txn_ids: string[];
  close_txn_ids: string[];
};

export type Transaction = {
  id: string;
  source: string;
  account_id: string;
  ts: string;
  symbol: string;
  underlying: string;
  asset_type: string;
  side: string;
  open_close: string;
  qty: number;
  price: number;
  fees: number;
  amount: number;
  description: string;
  external_id: string;
  strategy_tag: string;
};

export type Position = {
  symbol: string;
  underlying: string;
  asset_type: string;
  qty: number;
  avg_cost: number;
  market_value?: number | null;
  unrealized_pnl?: number | null;
  account_id: string;
  source: string;
};

export type PeriodSummary = {
  period: string;
  start: string;
  end: string;
  realized_pnl: number;
  fees: number;
  dividends: number;
  interest: number;
  net_deposits: number;
  net_cash_flow: number;
  trade_count: number;
  closed_lots: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  expectancy: number;
  profit_factor: number;
  gross_bought: number;
  gross_sold: number;
  turnover: number;
  avg_hold_days: number;
  options_share: number;
  concentration_top_symbol: string;
  concentration_top_share: number;
  by_underlying: Driver[];
  by_asset_type: Driver[];
  by_strategy: Driver[];
  by_weekday: Driver[];
  largest_wins: ClosedLot[];
  largest_losses: ClosedLot[];
  ending_equity?: number | null;
  ending_cash?: number | null;
};

export type ToplineMetric = {
  metric: string;
  label: string;
  a: number;
  b: number;
  delta: number;
  pct: number | null;
  format: 'usd' | 'int' | 'pct' | 'days';
};

export type VarianceDriver = {
  dimension: 'underlying' | 'asset_type' | 'strategy' | 'weekday';
  key: string;
  a: number;
  b: number;
  delta: number;
  contribution_pct: number;
  evidence_lot_ids: string[];
  note: string;
};

export type VarianceReport = {
  a: PeriodSummary;
  b: PeriodSummary;
  topline: ToplineMetric[];
  drivers: VarianceDriver[];
  bridge: { key: string; value: number }[];
  facts: string[];
  behaviour_flags: string[];
};

export type Overview = {
  has_data: boolean;
  sources: { source: string; account_id: string; count: number; start: string; end: string }[];
  transactions: number;
  periods: string[];
  latest_period: string | null;
  equity_curve: { period: string; realized_cum: number; net_deposits_cum: number }[];
  positions: Position[];
};

export type ProposedTrade = {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  qty: number;
  order_type: 'MKT' | 'LMT';
  limit_price?: number | null;
  tif: 'DAY' | 'GTC';
  rationale: string;
};

export type Report = {
  headline: string;
  what_changed: string[];
  why: string[];
  /** Optional macro/news framing; bullets may end with a "[source: https://...]" tag. */
  market_context?: string[];
  /** Per top-driver company context (earnings, guidance, price move) from Tavily. */
  company_changes?: { symbol: string; text: string; sources: string[] }[];
  drivers: { name: string; contribution_pct: number; detail: string; evidence: string[] }[];
  behaviour: string[];
  advisements: {
    title: string;
    detail: string;
    priority: 'high' | 'medium' | 'low';
    evidence: string[];
  }[];
  proposed_trades: ProposedTrade[];
  prior_insight_review: {
    insight_id: string;
    text: string;
    verdict: 'followed' | 'ignored' | 'validated' | 'invalidated' | 'unclear';
    note: string;
  }[];
  confidence: number;
  sources: string[];
};

export type RunSummary = {
  id: string;
  created_at: string;
  period_a: string;
  period_b: string;
  status: string;
  headline: string | null;
  model: string | null;
  latency_ms: number | null;
  prism_session_id: string | null;
};

export type Insight = {
  id: string;
  run_id: string;
  created_at: string;
  kind: string;
  text: string;
  evidence: string[];
  status: string;
};

export type AgentEvent =
  | { type: 'status'; message: string }
  | { type: 'tool_call'; name: string; input: unknown }
  | { type: 'tool_result'; name: string; summary: string }
  | { type: 'text'; delta: string }
  | {
      type: 'final';
      run_id: string;
      report: Report;
      prism: { session_id: string; trajectory_url?: string | null; traced: boolean };
      model: string;
      latency_ms: number;
      fallback: boolean;
    }
  | { type: 'error'; message: string };

export type IbkrStatus = {
  configured: boolean;
  gateway_url: string;
  reachable: boolean;
  authenticated: boolean;
  connected: boolean;
  accounts: string[];
  selected_account: string | null;
  paper: boolean;
  login_url: string;
};

export type RobinhoodStatus = { logged_in: boolean; username?: string; message: string };

export type OrderPreview = {
  order_id: string;
  conid: number | string;
  preview: Record<string, unknown>;
  warnings: string[];
};

export type OrderStatus =
  | 'proposed'
  | 'previewed'
  | 'submitted'
  | 'filled'
  | 'partially_filled'
  | 'cancelled'
  | 'rejected'
  | 'error';

export type Order = {
  id: string;
  created_at: string;
  run_id: string | null;
  account_id: string;
  symbol: string;
  conid: number | null;
  side: 'BUY' | 'SELL';
  qty: number;
  order_type: 'MKT' | 'LMT';
  limit_price: number | null;
  tif: string;
  status: OrderStatus;
  broker_order_id: string | null;
  rationale: string;
  preview: {
    commission?: number | null;
    equity_with_loan_before?: number | null;
    equity_with_loan_after?: number | null;
    amount?: string | null;
    warnings: string[];
  } | null;
  fill: { price: number | null; qty: number | null; time: string | null } | null;
  mark: number | null;
  pnl_since_fill: number | null;
  messages: string[];
};

export type Performance = {
  monthly: {
    period: string;
    realized_pnl: number;
    cum_realized: number;
    net_deposits: number;
    cum_net_deposits: number;
    fees: number;
    trade_count: number;
    win_rate: number;
  }[];
  stats: {
    total_realized: number;
    total_fees: number;
    total_net_deposits: number;
    months: number;
    best_period: string | null;
    worst_period: string | null;
    max_drawdown: number;
    win_rate: number;
    profit_factor: number;
    expectancy: number;
    avg_hold_days: number;
  };
  by_underlying: Driver[];
  positions: Position[];
};

export type OrderTicket = {
  symbol: string;
  side: 'BUY' | 'SELL';
  qty: number;
  order_type: 'MKT' | 'LMT';
  limit_price?: number | null;
  tif?: 'DAY' | 'GTC';
  run_id?: string;
  rationale?: string;
};

export type OrderPlaceResult = {
  order_id: string;
  status: string;
  broker_order_id: string | null;
  messages: string[];
};

/* ------------------------------------------------------------------ */

const BASE = '/api';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, init);
  } catch {
    throw new ApiError('Cannot reach the backend at localhost:8000', 0);
  }
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : typeof body === 'string' && body
          ? body
          : `${res.status} ${res.statusText}`;
    throw new ApiError(detail, res.status);
  }
  return body as T;
}

function get<T>(path: string): Promise<T> {
  return request<T>(path);
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/* ---- health / status ---- */
export const getHealth = () => get<Health>('/health');
export const getPrismStatus = () => get<PrismStatus>('/prism/status');

/* ---- ingest ---- */
export const ingestDemo = () => post<IngestResult>('/ingest/demo');
export const resetData = () => post<{ ok: boolean }>('/ingest/reset');

export function ingestCsv(file: File, source?: string): Promise<IngestResult> {
  const fd = new FormData();
  fd.append('file', file);
  if (source && source !== 'auto') fd.append('source', source);
  return request<IngestResult>('/ingest/csv', { method: 'POST', body: fd });
}

/* ---- portfolio / analytics ---- */
export const getOverview = () => get<Overview>('/portfolio/overview');
export const getPeriods = () => get<{ periods: PeriodSummary[] }>('/periods');
export const getPeriod = (p: string) => get<PeriodSummary>(`/periods/${encodeURIComponent(p)}`);
export const comparePeriods = (a: string, b: string) =>
  get<VarianceReport>(
    `/periods/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`,
  );
export const getPerformance = () => get<Performance>('/performance');
export const getLots = (period: string) =>
  get<{ lots: ClosedLot[] }>(`/lots?period=${encodeURIComponent(period)}&limit=200`);

/* ---- agent ---- */
export const startAnalysis = (a: string, b: string, question?: string) =>
  post<{ run_id: string }>('/analyze', { a, b, question: question || undefined });
export const getRuns = () => get<{ runs: RunSummary[] }>('/runs');
export const getRun = (id: string) =>
  get<{ run: RunSummary; report: Report | null; events: AgentEvent[] }>(
    `/runs/${encodeURIComponent(id)}`,
  );
export const getInsights = () => get<{ insights: Insight[] }>('/insights');
export const patchInsight = (id: string, status: string) =>
  request<Insight>(`/insights/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });

/**
 * Subscribe to the SSE trace of a run. Returns an unsubscribe function.
 */
export function subscribeToRun(
  runId: string,
  onEvent: (e: AgentEvent) => void,
  onError?: (message: string) => void,
): () => void {
  const es = new EventSource(`${BASE}/analyze/${encodeURIComponent(runId)}/events`);
  let closed = false;
  const close = () => {
    if (!closed) {
      closed = true;
      es.close();
    }
  };
  es.onmessage = (msg) => {
    if (!msg.data) return;
    let parsed: AgentEvent;
    try {
      parsed = JSON.parse(msg.data) as AgentEvent;
    } catch {
      return;
    }
    onEvent(parsed);
    if (parsed.type === 'final' || parsed.type === 'error') close();
  };
  es.onerror = () => {
    // The server closes the stream after `final`; that surfaces here as an error.
    if (!closed) {
      close();
      onError?.('Trace stream closed');
    }
  };
  return close;
}

/* ---- brokers: IBKR ---- */
export const getIbkrStatus = () => get<IbkrStatus>('/brokers/ibkr/status');
export const selectIbkrAccount = (account_id: string) =>
  post<IbkrStatus>('/brokers/ibkr/select_account', { account_id });
export const syncIbkr = () =>
  post<IngestResult & { positions: Position[] }>('/brokers/ibkr/sync');
export const ibkrFlex = (token: string, query_id: string) =>
  post<IngestResult>('/brokers/ibkr/flex', { token, query_id });
export const previewTicket = (t: OrderTicket) =>
  post<OrderPreview>('/brokers/ibkr/orders/preview', {
    symbol: t.symbol,
    side: t.side,
    qty: t.qty,
    order_type: t.order_type,
    limit_price: t.limit_price ?? undefined,
    tif: t.tif ?? 'DAY',
    run_id: t.run_id,
    rationale: t.rationale,
  });
export const previewOrder = (t: ProposedTrade, run_id?: string) =>
  previewTicket({ ...t, run_id, rationale: t.rationale });
export const placeOrder = (order_id: string) =>
  post<OrderPlaceResult>('/brokers/ibkr/orders/place', { order_id });
export const getOrders = () => get<{ orders: Order[] }>('/brokers/ibkr/orders');
export const cancelOrder = (order_id: string) =>
  request<{ ok?: boolean; status?: string }>(
    `/brokers/ibkr/orders/${encodeURIComponent(order_id)}`,
    { method: 'DELETE' },
  );

/* ---- brokers: Robinhood ---- */
export const robinhoodLogin = (username: string, password: string, mfa_code?: string) =>
  post<{ status: 'ok' | 'challenge' | 'error'; message: string; challenge_type?: string }>(
    '/brokers/robinhood/login',
    { username, password, mfa_code: mfa_code || undefined },
  );
export const getRobinhoodStatus = () => get<RobinhoodStatus>('/brokers/robinhood/status');
export const syncRobinhood = () => post<IngestResult>('/brokers/robinhood/sync');
