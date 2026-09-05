import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import clsx from 'clsx';
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  AlertTriangle,
  Ban,
  Building2,
  ExternalLink,
  LineChart as LineChartIcon,
  ListOrdered,
  Play,
  RefreshCw,
  ScanEye,
  Sparkles,
} from 'lucide-react';
import type {
  IbkrStatus,
  Order,
  OrderTicket,
  Performance,
  ProposedTrade,
  RunSummary,
} from '../api';
import {
  cancelOrder,
  getOrders,
  getPerformance,
  getRun,
  getRuns,
  placeOrder,
  previewTicket,
} from '../api';
import { Button, Card, ErrorBox, Field, Pill, Spinner, inputCls } from './Pills';
import type { Tone } from './Pills';
import { dateTime, days, int, pct, periodLabel, relativeTime, signedUsd, usd } from '../lib/format';

const STATUS_TONE: Record<string, Tone> = {
  proposed: 'slate',
  previewed: 'sky',
  submitted: 'amber',
  filled: 'emerald',
  partially_filled: 'amber',
  cancelled: 'slate',
  rejected: 'rose',
  error: 'rose',
};

const LIVE_STATUSES = new Set(['submitted', 'previewed', 'partially_filled']);

/* ------------------------------------------------------------------ */

function TradeRow({
  trade,
  runId,
  ready,
  onChanged,
}: {
  trade: ProposedTrade;
  runId?: string;
  ready: boolean;
  onChanged: () => void;
}) {
  const [orderId, setOrderId] = useState<string | null>(null);
  const [busy, setBusy] = useState<'preview' | 'place' | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const doPreview = async () => {
    setBusy('preview');
    setError(null);
    try {
      const p = await previewTicket({ ...trade, run_id: runId, rationale: trade.rationale });
      setOrderId(p.order_id);
      const w = p.warnings?.length ? ` · ${p.warnings.join('; ')}` : '';
      setNote(`Previewed${w}`);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const doPlace = async () => {
    if (!orderId) return;
    setBusy('place');
    setError(null);
    try {
      const r = await placeOrder(orderId);
      setNote(`${r.status}${r.broker_order_id ? ` · #${r.broker_order_id}` : ''}`);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const buy = trade.side === 'BUY';

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={clsx(
            'rounded px-2 py-0.5 text-xs font-bold',
            buy ? 'bg-emerald-500/15 text-emerald-300' : 'bg-rose-500/15 text-rose-300',
          )}
        >
          {trade.side}
        </span>
        <span className="text-lg font-bold text-slate-50">{trade.symbol}</span>
        <span className="text-sm text-slate-400">
          {trade.qty} × {trade.order_type}
          {trade.order_type === 'LMT' && trade.limit_price != null
            ? ` @ ${usd(trade.limit_price, 2)}`
            : ''}
        </span>
        <Pill tone="slate">{trade.tif}</Pill>
        <div className="ml-auto flex items-center gap-2">
          <Button onClick={doPreview} disabled={busy !== null}>
            {busy === 'preview' ? <Spinner /> : <ScanEye size={14} />} Preview
          </Button>
          <span title={!ready ? 'Connect the IBKR paper gateway first' : ''}>
            <Button
              variant="primary"
              onClick={doPlace}
              disabled={!orderId || !ready || busy !== null}
            >
              {busy === 'place' ? <Spinner /> : <Play size={14} />} Execute on paper
            </Button>
          </span>
        </div>
      </div>
      {trade.rationale && <p className="mt-2 text-sm text-slate-400">{trade.rationale}</p>}
      {note && <p className="mt-2 text-xs font-semibold text-sky-300">{note}</p>}
      <ErrorBox message={error} />
    </div>
  );
}

/* ------------------------------------------------------------------ */

function ManualTicket({ ready, onChanged }: { ready: boolean; onChanged: () => void }) {
  const [t, setT] = useState<OrderTicket>({
    symbol: '',
    side: 'BUY',
    qty: 1,
    order_type: 'MKT',
    limit_price: null,
    tif: 'DAY',
  });
  const [orderId, setOrderId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (kind: 'preview' | 'place') => {
    setBusy(true);
    setError(null);
    try {
      if (kind === 'preview') {
        const p = await previewTicket({ ...t, rationale: 'manual ticket' });
        setOrderId(p.order_id);
        setNote(`Previewed${p.warnings?.length ? ` · ${p.warnings.join('; ')}` : ''}`);
      } else if (orderId) {
        const r = await placeOrder(orderId);
        setNote(`${r.status}${r.broker_order_id ? ` · #${r.broker_order_id}` : ''}`);
      }
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
      <div className="mb-3 text-xs font-semibold tracking-wide text-slate-500 uppercase">
        Manual ticket
      </div>
      <div className="grid gap-2 sm:grid-cols-5">
        <Field label="Symbol">
          <input
            className={inputCls}
            placeholder="NVDA"
            value={t.symbol}
            onChange={(e) => setT({ ...t, symbol: e.target.value.toUpperCase() })}
          />
        </Field>
        <Field label="Side">
          <select
            className={inputCls}
            value={t.side}
            onChange={(e) => setT({ ...t, side: e.target.value as 'BUY' | 'SELL' })}
          >
            <option>BUY</option>
            <option>SELL</option>
          </select>
        </Field>
        <Field label="Qty">
          <input
            className={inputCls}
            type="number"
            min={1}
            value={t.qty}
            onChange={(e) => setT({ ...t, qty: Number(e.target.value) })}
          />
        </Field>
        <Field label="Type">
          <select
            className={inputCls}
            value={t.order_type}
            onChange={(e) => setT({ ...t, order_type: e.target.value as 'MKT' | 'LMT' })}
          >
            <option>MKT</option>
            <option>LMT</option>
          </select>
        </Field>
        <Field label="Limit">
          <input
            className={inputCls}
            type="number"
            step="0.01"
            disabled={t.order_type !== 'LMT'}
            value={t.limit_price ?? ''}
            onChange={(e) =>
              setT({ ...t, limit_price: e.target.value === '' ? null : Number(e.target.value) })
            }
          />
        </Field>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button onClick={() => run('preview')} disabled={busy || !t.symbol || t.qty <= 0}>
          {busy ? <Spinner /> : <ScanEye size={14} />} Preview
        </Button>
        <span title={!ready ? 'Connect the IBKR paper gateway first' : ''}>
          <Button variant="primary" onClick={() => run('place')} disabled={busy || !orderId || !ready}>
            <Play size={14} /> Execute on paper
          </Button>
        </span>
        {note && <span className="text-xs font-semibold text-sky-300">{note}</span>}
      </div>
      <ErrorBox message={error} />
    </div>
  );
}

/* ------------------------------------------------------------------ */

function OrdersTable({ orders, onCancel }: { orders: Order[]; onCancel: (id: string) => void }) {
  if (!orders.length) {
    return (
      <p className="py-6 text-center text-sm text-slate-500">
        No orders yet. Preview and execute a proposal above.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm whitespace-nowrap">
        <thead>
          <tr className="border-b border-slate-800 text-[11px] tracking-wide text-slate-500 uppercase">
            <th className="py-2 pr-4 font-medium">Order</th>
            <th className="py-2 pr-4 font-medium">Status</th>
            <th className="py-2 pr-4 text-right font-medium">Fill</th>
            <th className="py-2 pr-4 font-medium">Filled at</th>
            <th className="py-2 pr-4 text-right font-medium">Mark</th>
            <th className="py-2 pr-4 text-right font-medium">P&L</th>
            <th className="py-2 pr-4 font-medium">Messages</th>
            <th className="py-2 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => {
            const cancellable = ['submitted', 'previewed', 'partially_filled'].includes(o.status);
            return (
              <tr key={o.id} className="border-b border-slate-800/60 last:border-0 align-top">
                <td className="py-2.5 pr-4">
                  <div className="flex items-center gap-1.5">
                    <span
                      className={clsx(
                        'rounded px-1.5 py-0.5 text-[10px] font-bold',
                        o.side === 'BUY'
                          ? 'bg-emerald-500/15 text-emerald-300'
                          : 'bg-rose-500/15 text-rose-300',
                      )}
                    >
                      {o.side}
                    </span>
                    <span className="font-semibold text-slate-100">{o.symbol}</span>
                    <span className="text-slate-500">
                      {o.qty} {o.order_type}
                      {o.limit_price != null ? ` @ ${usd(o.limit_price, 2)}` : ''}
                    </span>
                  </div>
                  <div
                    className="mt-0.5 text-[11px] text-slate-600"
                    title={dateTime(o.created_at)}
                  >
                    {relativeTime(o.created_at)}
                  </div>
                </td>
                <td className="py-2.5 pr-4">
                  <Pill tone={STATUS_TONE[o.status] ?? 'slate'}>{o.status}</Pill>
                </td>
                <td className="py-2.5 pr-4 text-right text-slate-200 tabular-nums">
                  {o.fill?.price != null ? usd(o.fill.price, 2) : '—'}
                  {o.fill?.qty != null && (
                    <span className="ml-1 text-[11px] text-slate-600">×{o.fill.qty}</span>
                  )}
                </td>
                <td
                  className="py-2.5 pr-4 text-slate-500"
                  title={o.fill?.time ? dateTime(o.fill.time) : undefined}
                >
                  {o.fill?.time ? relativeTime(o.fill.time) : '—'}
                </td>
                <td className="py-2.5 pr-4 text-right text-slate-300 tabular-nums">
                  {o.mark != null ? usd(o.mark, 2) : '—'}
                </td>
                <td
                  className={clsx(
                    'py-2.5 pr-4 text-right font-semibold tabular-nums',
                    o.pnl_since_fill == null
                      ? 'text-slate-600'
                      : o.pnl_since_fill >= 0
                        ? 'text-emerald-400'
                        : 'text-rose-400',
                  )}
                >
                  {o.pnl_since_fill != null ? signedUsd(o.pnl_since_fill, 2) : '—'}
                </td>
                <td className="max-w-[18rem] py-2.5 pr-4 text-xs whitespace-normal text-slate-500">
                  {o.messages?.length ? o.messages.join(' · ') : o.rationale || '—'}
                </td>
                <td className="py-2.5">
                  {cancellable && (
                    <Button
                      variant="ghost"
                      className="!px-2 !py-1 text-xs"
                      onClick={() => onCancel(o.id)}
                    >
                      <Ban size={12} /> Cancel
                    </Button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ */

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="text-[11px] font-medium tracking-wider text-slate-500 uppercase">{label}</div>
      <div className={clsx('mt-1.5 text-2xl font-bold tracking-tight tabular-nums', tone ?? 'text-slate-50')}>
        {value}
      </div>
    </div>
  );
}

function PerformancePanel({ perf }: { perf: Performance }) {
  const s = perf.stats;
  const monthly = (perf.monthly ?? []).map((m) => ({ ...m, label: periodLabel(m.period) }));
  const top = (perf.by_underlying ?? []).slice(0, 10);
  const maxAbs = Math.max(...top.map((d) => Math.abs(d.realized_pnl)), 1);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat
          label="Total realized"
          value={signedUsd(s?.total_realized)}
          tone={(s?.total_realized ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}
        />
        <Stat label="Max drawdown" value={usd(s?.max_drawdown)} tone="text-rose-400" />
        <Stat label="Win rate" value={pct(s?.win_rate)} />
        <Stat label="Profit factor" value={(s?.profit_factor ?? 0).toFixed(2)} />
        <Stat label="Expectancy" value={signedUsd(s?.expectancy)} />
        <Stat label="Avg hold" value={days(s?.avg_hold_days)} />
      </div>

      <div className="grid gap-4 xl:grid-cols-5">
        <Card className="xl:col-span-3" title="Monthly realized P&L" subtitle="Bars = month, line = cumulative">
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={monthly} margin={{ top: 10, right: 8, left: 4, bottom: 4 }}>
                <CartesianGrid stroke="#1e293b" vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={{ fill: '#64748b', fontSize: 11 }}
                  axisLine={{ stroke: '#1e293b' }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: '#64748b', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={64}
                  tickFormatter={(v: number) => usd(v)}
                />
                <ReferenceLine y={0} stroke="#334155" />
                <Tooltip
                  contentStyle={{
                    background: '#0f172a',
                    border: '1px solid #334155',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  labelStyle={{ color: '#e2e8f0' }}
                  formatter={(v: unknown, n: unknown) => [usd(Number(v)), String(n)]}
                />
                <Bar dataKey="realized_pnl" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                  {monthly.map((m, i) => (
                    <Cell key={i} fill={m.realized_pnl >= 0 ? '#10b981' : '#f43f5e'} />
                  ))}
                </Bar>
                <Line
                  type="monotone"
                  dataKey="cum_realized"
                  stroke="#38bdf8"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card className="xl:col-span-2" title="By underlying" subtitle="Whole-book realized P&L, top 10">
          {top.length === 0 ? (
            <p className="py-6 text-center text-sm text-slate-500">No data.</p>
          ) : (
            <div className="space-y-2">
              {top.map((d) => (
                <div key={d.key} className="flex items-center gap-3">
                  <div className="w-16 shrink-0 truncate text-right text-xs font-semibold text-slate-300">
                    {d.key}
                  </div>
                  <div className="relative h-5 flex-1 rounded bg-slate-800/40">
                    <div className="absolute inset-y-0 left-1/2 w-px bg-slate-700" />
                    <div
                      className={clsx(
                        'absolute h-5 rounded',
                        d.realized_pnl >= 0 ? 'bg-emerald-500' : 'bg-rose-500',
                      )}
                      style={{
                        width: `${(Math.abs(d.realized_pnl) / maxAbs) * 50}%`,
                        left: d.realized_pnl >= 0 ? '50%' : undefined,
                        right: d.realized_pnl < 0 ? '50%' : undefined,
                      }}
                    />
                  </div>
                  <div
                    className={clsx(
                      'w-20 shrink-0 text-right text-xs font-semibold tabular-nums',
                      d.realized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400',
                    )}
                  >
                    {signedUsd(d.realized_pnl)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {perf.positions?.length > 0 && (
        <Card title="Open positions" subtitle={`${int(perf.positions.length)} instruments`}>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm whitespace-nowrap">
              <thead>
                <tr className="border-b border-slate-800 text-[11px] tracking-wide text-slate-500 uppercase">
                  <th className="py-2 pr-4 font-medium">Symbol</th>
                  <th className="py-2 pr-4 font-medium">Type</th>
                  <th className="py-2 pr-4 text-right font-medium">Qty</th>
                  <th className="py-2 pr-4 text-right font-medium">Avg cost</th>
                  <th className="py-2 pr-4 text-right font-medium">Market value</th>
                  <th className="py-2 text-right font-medium">Unrealized</th>
                </tr>
              </thead>
              <tbody>
                {perf.positions.slice(0, 25).map((p, i) => (
                  <tr key={i} className="border-b border-slate-800/60 last:border-0">
                    <td className="py-2 pr-4 font-semibold text-slate-100">{p.symbol}</td>
                    <td className="py-2 pr-4 text-slate-500">{p.asset_type}</td>
                    <td className="py-2 pr-4 text-right text-slate-300 tabular-nums">{p.qty}</td>
                    <td className="py-2 pr-4 text-right text-slate-300 tabular-nums">
                      {usd(p.avg_cost, 2)}
                    </td>
                    <td className="py-2 pr-4 text-right text-slate-300 tabular-nums">
                      {p.market_value != null ? usd(p.market_value) : '—'}
                    </td>
                    <td
                      className={clsx(
                        'py-2 text-right font-semibold tabular-nums',
                        p.unrealized_pnl == null
                          ? 'text-slate-600'
                          : p.unrealized_pnl >= 0
                            ? 'text-emerald-400'
                            : 'text-rose-400',
                      )}
                    >
                      {p.unrealized_pnl != null ? signedUsd(p.unrealized_pnl) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

export function TradeDesk({
  ibkr,
  refreshIbkr,
  reloadToken,
  onGoConnect,
}: {
  ibkr: IbkrStatus | null;
  refreshIbkr: () => Promise<void>;
  reloadToken: number;
  onGoConnect: () => void;
}) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [perf, setPerf] = useState<Performance | null>(null);
  const [proposed, setProposed] = useState<{ run: RunSummary; trades: ProposedTrade[] } | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const ready = Boolean(ibkr?.authenticated && ibkr?.selected_account);

  const loadOrders = useCallback(async () => {
    try {
      const r = await getOrders();
      setOrders(r.orders ?? []);
    } catch {
      /* gateway may be down; keep stale */
    }
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [ordersRes, perfRes, runsRes] = await Promise.allSettled([
      getOrders(),
      getPerformance(),
      getRuns(),
    ]);
    if (ordersRes.status === 'fulfilled') setOrders(ordersRes.value.orders ?? []);
    if (perfRes.status === 'fulfilled') setPerf(perfRes.value);
    else setError(perfRes.reason?.message ?? null);
    if (runsRes.status === 'fulfilled') {
      // Prefer the most recent completed run that actually proposes trades (a calm month may
      // legitimately propose nothing); fall back to the latest completed run.
      const doneRuns = (runsRes.value.runs ?? []).filter(
        (r) => r.status === 'done' || r.status === 'complete' || r.status === 'ok',
      );
      let picked: { run: (typeof doneRuns)[number]; trades: ProposedTrade[] } | null = null;
      for (const run of doneRuns.slice(0, 6)) {
        try {
          const d = await getRun(run.id);
          const trades = d.report?.proposed_trades ?? [];
          if (!picked) picked = { run, trades };
          if (trades.length > 0) {
            picked = { run, trades };
            break;
          }
        } catch {
          /* try the next run */
        }
      }
      setProposed(picked);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll, reloadToken]);

  const hasLive = useMemo(() => orders.some((o) => LIVE_STATUSES.has(o.status)), [orders]);

  useEffect(() => {
    if (!hasLive) return;
    timer.current = window.setInterval(loadOrders, 10_000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [hasLive, loadOrders]);

  const doCancel = async (id: string) => {
    try {
      await cancelOrder(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    loadOrders();
  };

  return (
    <div className="space-y-5">
      {/* Header strip */}
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-800 bg-slate-900/50 px-5 py-4">
        <Building2 size={18} className="text-slate-400" />
        <span className="font-semibold text-slate-100">IBKR paper desk</span>
        <Pill tone={ready ? 'emerald' : ibkr?.reachable ? 'amber' : 'rose'}>
          {ready ? (ibkr?.demo ? 'demo connection · simulated fills' : 'connected') : ibkr?.reachable ? 'gateway up · not authenticated' : 'offline'}
        </Pill>
        {ibkr?.selected_account && <Pill tone="sky">{ibkr.selected_account}</Pill>}
        {ibkr?.paper && <Pill tone="violet">PAPER</Pill>}
        <div className="ml-auto flex items-center gap-2">
          {!ready && (
            <Button onClick={onGoConnect}>
              <ExternalLink size={14} /> Connect IBKR
            </Button>
          )}
          <Button
            variant="ghost"
            onClick={async () => {
              await refreshIbkr();
              await loadAll();
            }}
          >
            <RefreshCw size={14} /> Refresh
          </Button>
        </div>
      </div>

      {!ready && (
        <div className="flex items-start gap-3 rounded-2xl border border-amber-500/35 bg-amber-500/10 px-5 py-4">
          <AlertTriangle size={18} className="mt-0.5 shrink-0 text-amber-400" />
          <div className="min-w-0">
            <p className="font-semibold text-amber-200">
              IBKR paper gateway is not connected — previews and executions will fail.
            </p>
            <ol className="mt-2 space-y-1 text-sm text-amber-100/80">
              <li>
                <span className="mr-1.5 font-bold text-amber-300">1.</span>
                Run <code className="rounded bg-black/30 px-1.5 py-0.5 font-mono text-[12px]">./scripts/ibkr-gateway.sh</code>
              </li>
              <li>
                <span className="mr-1.5 font-bold text-amber-300">2.</span>
                Open{' '}
                <a
                  href="https://localhost:5001"
                  target="_blank"
                  rel="noreferrer"
                  className="rounded font-mono text-[12px] underline decoration-amber-400/50 underline-offset-2 hover:text-amber-50"
                >
                  https://localhost:5001
                </a>
              </li>
              <li>
                <span className="mr-1.5 font-bold text-amber-300">3.</span>
                Log in with the paper (<span className="font-mono text-[12px]">DU…</span>) username
              </li>
              <li>
                <span className="mr-1.5 font-bold text-amber-300">4.</span>
                Click <span className="font-semibold">Refresh</span> above
              </li>
            </ol>
          </div>
        </div>
      )}

      <ErrorBox message={error} />

      {/* Proposed by the agent */}
      <Card
        title="Proposed by the agent"
        subtitle={
          proposed
            ? `From run ${proposed.run.id.slice(0, 8)} · ${periodLabel(proposed.run.period_a)} → ${periodLabel(proposed.run.period_b)}`
            : 'No completed run with proposals yet.'
        }
        icon={<Sparkles size={18} />}
      >
        <div className="space-y-3">
          {proposed?.trades?.length ? (
            proposed.trades.map((t) => (
              <TradeRow
                key={t.id}
                trade={t}
                runId={proposed.run.id}
                ready={ready}
                onChanged={loadOrders}
              />
            ))
          ) : (
            <p className="text-sm text-slate-500">
              Run “Explain the change” to get trade proposals, or use the manual ticket below.
            </p>
          )}
          <ManualTicket ready={ready} onChanged={loadOrders} />
        </div>
      </Card>

      {/* What happened */}
      <Card
        title="What happened"
        subtitle={hasLive ? 'Auto-refreshing every 10s while orders are live' : 'Order history'}
        icon={<ListOrdered size={18} />}
        actions={
          <button onClick={loadOrders} className="text-slate-500 hover:text-slate-300" title="Refresh">
            <RefreshCw size={14} />
          </button>
        }
      >
        <OrdersTable orders={orders} onCancel={doCancel} />
      </Card>

      {/* Performance */}
      <div>
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold tracking-wide text-slate-400 uppercase">
          <LineChartIcon size={14} /> Performance
        </h2>
        {loading && !perf && (
          <div className="py-10 text-center text-sm text-slate-500">Loading performance…</div>
        )}
        {perf && <PerformancePanel perf={perf} />}
        {!loading && !perf && (
          <p className="py-6 text-center text-sm text-slate-500">
            No performance data — load a book on the Connect tab.
          </p>
        )}
      </div>
    </div>
  );
}
