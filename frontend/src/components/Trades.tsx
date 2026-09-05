import { useState } from 'react';
import clsx from 'clsx';
import { CheckCircle2, Play, ScanEye } from 'lucide-react';
import type { OrderPlaceResult, OrderPreview, ProposedTrade } from '../api';
import { placeOrder, previewOrder } from '../api';
import { Button, ErrorBox, Pill, Spinner } from './Pills';
import { usd } from '../lib/format';

function previewLines(p: OrderPreview): string[] {
  const out: string[] = [];
  const src = p.preview ?? {};
  const walk = (obj: unknown, prefix = '') => {
    if (!obj || typeof obj !== 'object') return;
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      if (out.length >= 8) return;
      if (v && typeof v === 'object') walk(v, prefix ? `${prefix}.${k}` : k);
      else if (v !== null && v !== '') out.push(`${prefix ? prefix + '.' : ''}${k}: ${String(v)}`);
    }
  };
  walk(src);
  return out;
}

function TradeCard({
  trade,
  runId,
  ibkrReady,
}: {
  trade: ProposedTrade;
  runId?: string;
  ibkrReady: boolean;
}) {
  const [preview, setPreview] = useState<OrderPreview | null>(null);
  const [placed, setPlaced] = useState<OrderPlaceResult | null>(null);
  const [busy, setBusy] = useState<'preview' | 'place' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const doPreview = async () => {
    setBusy('preview');
    setError(null);
    try {
      setPreview(await previewOrder(trade, runId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const doPlace = async () => {
    if (!preview) return;
    setBusy('place');
    setError(null);
    try {
      setPlaced(await placeOrder(preview.order_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const buy = trade.side === 'BUY';

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
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
      </div>

      {trade.rationale && <p className="mt-2 text-sm text-slate-400">{trade.rationale}</p>}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button onClick={doPreview} disabled={busy !== null}>
          {busy === 'preview' ? <Spinner /> : <ScanEye size={14} />}
          Preview
        </Button>
        <span title={!ibkrReady ? 'Connect the IBKR paper gateway on the Connect tab first' : ''}>
          <Button
            variant="primary"
            onClick={doPlace}
            disabled={!preview || !ibkrReady || busy !== null || placed !== null}
          >
            {busy === 'place' ? <Spinner /> : <Play size={14} />}
            Execute on IBKR paper
          </Button>
        </span>
        {!ibkrReady && <span className="text-xs text-slate-500">IBKR not connected</span>}
      </div>

      <ErrorBox message={error} />

      {preview && (
        <div className="mt-3 rounded-lg border border-slate-800 bg-slate-900/70 p-3">
          <div className="mb-1.5 text-xs font-semibold tracking-wide text-slate-400 uppercase">
            What-if preview · order {preview.order_id.slice(0, 8)}
          </div>
          <ul className="space-y-0.5 font-mono text-[11px] text-slate-400">
            {previewLines(preview).map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
          {preview.warnings?.length > 0 && (
            <ul className="mt-2 space-y-1">
              {preview.warnings.map((w, i) => (
                <li key={i} className="text-xs text-amber-300">
                  ⚠ {w}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {placed && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-300">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
          <div>
            <div className="font-semibold">
              {placed.status}
              {placed.broker_order_id ? ` · broker #${placed.broker_order_id}` : ''}
            </div>
            {placed.messages?.map((m, i) => (
              <div key={i} className="text-xs opacity-80">
                {m}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function Trades({
  trades,
  runId,
  ibkrReady,
}: {
  trades: ProposedTrade[];
  runId?: string;
  ibkrReady: boolean;
}) {
  if (!trades?.length) return null;
  return (
    <div className="space-y-3">
      {trades.map((t) => (
        <TradeCard key={t.id} trade={t} runId={runId} ibkrReady={ibkrReady} />
      ))}
    </div>
  );
}
