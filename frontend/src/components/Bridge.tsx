import {
  Bar,
  BarChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { VarianceReport } from '../api';
import { signedUsd, usd } from '../lib/format';

type Row = {
  key: string;
  value: number;
  range: [number, number];
  kind: 'start' | 'end' | 'up' | 'down';
};

function buildRows(bridge: { key: string; value: number }[]): Row[] {
  const rows: Row[] = [];
  let running = 0;
  bridge.forEach((b, i) => {
    const isStart = i === 0;
    const isEnd = i === bridge.length - 1;
    const v = Number(b.value) || 0;
    if (isStart) {
      running = v;
      rows.push({ key: b.key, value: v, range: [Math.min(0, v), Math.max(0, v)], kind: 'start' });
    } else if (isEnd) {
      rows.push({ key: b.key, value: v, range: [Math.min(0, v), Math.max(0, v)], kind: 'end' });
    } else {
      const from = running;
      const to = running + v;
      running = to;
      rows.push({
        key: b.key,
        value: v,
        range: [Math.min(from, to), Math.max(from, to)],
        kind: v >= 0 ? 'up' : 'down',
      });
    }
  });
  return rows;
}

const COLORS: Record<Row['kind'], string> = {
  start: '#38bdf8',
  end: '#38bdf8',
  up: '#10b981',
  down: '#f43f5e',
};

function BridgeTooltip({ active, payload }: { active?: boolean; payload?: { payload: Row }[] }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs shadow-xl">
      <div className="font-semibold text-slate-100">{r.key}</div>
      <div className="mt-0.5 text-slate-400">
        {r.kind === 'start' || r.kind === 'end' ? usd(r.value) : signedUsd(r.value)}
      </div>
    </div>
  );
}

export function Bridge({ report }: { report: VarianceReport }) {
  const bridge = report.bridge ?? [];

  if (bridge.length < 2) {
    // Fallback: horizontal driver deltas.
    const drivers = (report.drivers ?? []).slice(0, 8);
    if (!drivers.length) {
      return <div className="py-10 text-center text-sm text-slate-500">No bridge data.</div>;
    }
    const max = Math.max(...drivers.map((d) => Math.abs(d.delta)), 1);
    return (
      <div className="space-y-2">
        {drivers.map((d) => (
          <div key={d.dimension + d.key} className="flex items-center gap-3">
            <div className="w-24 shrink-0 truncate text-right text-xs font-semibold text-slate-300">
              {d.key}
            </div>
            <div className="relative h-5 flex-1 rounded bg-slate-800/50">
              <div
                className={d.delta >= 0 ? 'absolute h-5 rounded bg-emerald-500' : 'absolute h-5 rounded bg-rose-500'}
                style={{
                  width: `${(Math.abs(d.delta) / max) * 50}%`,
                  left: d.delta >= 0 ? '50%' : undefined,
                  right: d.delta < 0 ? '50%' : undefined,
                }}
              />
            </div>
            <div className="w-24 shrink-0 text-xs font-semibold text-slate-400 tabular-nums">
              {signedUsd(d.delta)}
            </div>
          </div>
        ))}
      </div>
    );
  }

  const rows = buildRows(bridge);

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 12, right: 8, left: 4, bottom: 4 }}>
          <XAxis
            dataKey="key"
            tick={{ fill: '#64748b', fontSize: 11 }}
            axisLine={{ stroke: '#1e293b' }}
            tickLine={false}
            interval={0}
            angle={rows.length > 7 ? -30 : 0}
            textAnchor={rows.length > 7 ? 'end' : 'middle'}
            height={rows.length > 7 ? 56 : 30}
          />
          <YAxis
            tick={{ fill: '#64748b', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            width={64}
            tickFormatter={(v: number) => usd(v)}
          />
          <ReferenceLine y={0} stroke="#334155" />
          <Tooltip content={<BridgeTooltip />} cursor={{ fill: 'rgba(148,163,184,0.06)' }} />
          <Bar dataKey="range" radius={[3, 3, 3, 3]} isAnimationActive={false}>
            {rows.map((r, i) => (
              <Cell key={i} fill={COLORS[r.kind]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
