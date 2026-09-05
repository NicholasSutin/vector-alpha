import clsx from 'clsx';
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
import type { ToplineMetric } from '../api';
import { formatDelta, formatValue, signedPct } from '../lib/format';

/** Metrics where "up" is bad (fees, concentration, turnover...). */
const INVERTED = new Set(['fees', 'concentration', 'concentration_top_share', 'turnover']);

export function KpiTile({ m }: { m: ToplineMetric }) {
  const up = m.delta > 0;
  const flat = Math.abs(m.delta) < 1e-9;
  const inverted = INVERTED.has(m.metric);
  const good = flat ? null : inverted ? !up : up;

  const tone = good === null ? 'text-slate-400' : good ? 'text-emerald-400' : 'text-rose-400';
  const Icon = flat ? Minus : up ? ArrowUpRight : ArrowDownRight;

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 transition hover:border-slate-700">
      <div className="text-[11px] font-medium tracking-wider text-slate-500 uppercase">
        {m.label}
      </div>
      <div className="mt-2 text-2xl font-bold tracking-tight text-slate-50 tabular-nums">
        {formatValue(m.b, m.format)}
      </div>
      <div className={clsx('mt-1.5 flex items-center gap-1 text-sm font-semibold', tone)}>
        <Icon size={14} strokeWidth={2.5} />
        <span className="tabular-nums">{formatDelta(m.delta, m.format)}</span>
        {m.pct !== null && m.pct !== undefined && Number.isFinite(m.pct) && (
          <span className="text-xs font-medium opacity-80">({signedPct(m.pct)})</span>
        )}
      </div>
      <div className="mt-1 text-[11px] text-slate-600">
        was {formatValue(m.a, m.format)}
      </div>
    </div>
  );
}
