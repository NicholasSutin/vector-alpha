import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, GitCompare, Sparkles, TrendingDown } from 'lucide-react';
import type { AgentEvent, Overview, Report, VarianceReport } from '../api';
import { comparePeriods, startAnalysis, subscribeToRun } from '../api';
import { Button, Card, ErrorBox, Pill, Spinner, inputCls } from './Pills';
import { KpiTile } from './KpiTile';
import { Bridge } from './Bridge';
import { AgentTrace } from './AgentTrace';
import { ReportView } from './ReportView';
import { periodLabel, pct, signedUsd } from '../lib/format';

const DIM_TONE: Record<string, 'sky' | 'violet' | 'amber' | 'emerald'> = {
  underlying: 'sky',
  asset_type: 'violet',
  strategy: 'emerald',
  weekday: 'amber',
};

export function Explain({
  overview,
  ibkrReady,
  onRunFinished,
}: {
  overview: Overview | null;
  ibkrReady: boolean;
  onRunFinished: () => void;
}) {
  const periods = overview?.periods ?? [];
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [variance, setVariance] = useState<VarianceReport | null>(null);
  const [varErr, setVarErr] = useState<string | null>(null);
  const [varLoading, setVarLoading] = useState(false);

  const [question, setQuestion] = useState('');
  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [runMeta, setRunMeta] = useState<{
    runId: string;
    model: string;
    latency: number;
    fallback: boolean;
    prismSession: string | null;
    prismUrl: string | null;
  } | null>(null);
  const [runErr, setRunErr] = useState<string | null>(null);
  const unsubRef = useRef<(() => void) | null>(null);

  // default to the last two periods
  useEffect(() => {
    if (periods.length >= 2) {
      setA((cur) => (cur && periods.includes(cur) ? cur : periods[periods.length - 2]));
      setB((cur) => (cur && periods.includes(cur) ? cur : periods[periods.length - 1]));
    } else if (periods.length === 1) {
      setA(periods[0]);
      setB(periods[0]);
    }
  }, [periods.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadVariance = useCallback(async () => {
    if (!a || !b) return;
    setVarLoading(true);
    setVarErr(null);
    try {
      setVariance(await comparePeriods(a, b));
    } catch (e) {
      setVariance(null);
      setVarErr(e instanceof Error ? e.message : String(e));
    } finally {
      setVarLoading(false);
    }
  }, [a, b]);

  useEffect(() => {
    loadVariance();
  }, [loadVariance]);

  useEffect(() => () => unsubRef.current?.(), []);

  const run = async () => {
    if (!a || !b) return;
    unsubRef.current?.();
    setRunning(true);
    setEvents([]);
    setReport(null);
    setRunMeta(null);
    setRunErr(null);
    try {
      const { run_id } = await startAnalysis(a, b, question);
      unsubRef.current = subscribeToRun(
        run_id,
        (e) => {
          setEvents((prev) => [...prev, e]);
          if (e.type === 'final') {
            setReport(e.report);
            setRunMeta({
              runId: e.run_id,
              model: e.model,
              latency: e.latency_ms,
              fallback: e.fallback,
              prismSession: e.prism?.session_id ?? null,
              prismUrl: e.prism?.trajectory_url ?? null,
            });
            setRunning(false);
            onRunFinished();
          } else if (e.type === 'error') {
            setRunErr(e.message);
            setRunning(false);
          }
        },
        () => setRunning(false),
      );
    } catch (e) {
      setRunErr(e instanceof Error ? e.message : String(e));
      setRunning(false);
    }
  };

  if (!overview?.has_data) {
    return (
      <div className="flex min-h-[50vh] flex-col items-center justify-center rounded-2xl border border-dashed border-slate-800 bg-slate-900/30 p-12 text-center">
        <TrendingDown size={40} className="mb-4 text-slate-700" />
        <h2 className="text-xl font-semibold text-slate-200">No book connected yet</h2>
        <p className="mt-2 max-w-md text-slate-500">
          Load the demo book or connect a broker on the <span className="text-slate-300">Connect</span>{' '}
          tab, then come back to explain the change.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Period pickers + question + CTA */}
      <Card className="!p-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end">
          <div className="flex items-end gap-3">
            <label className="block">
              <span className="mb-1 block text-[11px] font-medium tracking-wider text-slate-500 uppercase">
                Baseline (A)
              </span>
              <select value={a} onChange={(e) => setA(e.target.value)} className={inputCls}>
                {periods.map((p) => (
                  <option key={p} value={p}>
                    {periodLabel(p)}
                  </option>
                ))}
              </select>
            </label>
            <ArrowRight size={18} className="mb-2.5 shrink-0 text-slate-600" />
            <label className="block">
              <span className="mb-1 block text-[11px] font-medium tracking-wider text-slate-500 uppercase">
                Current (B)
              </span>
              <select value={b} onChange={(e) => setB(e.target.value)} className={inputCls}>
                {periods.map((p) => (
                  <option key={p} value={p}>
                    {periodLabel(p)}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="min-w-0 flex-1">
            <span className="mb-1 block text-[11px] font-medium tracking-wider text-slate-500 uppercase">
              Question (optional)
            </span>
            <input
              className={inputCls}
              placeholder="e.g. Why did my win rate drop?"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !running && run()}
            />
          </label>

          <Button
            variant="primary"
            className="h-[38px] px-5 text-base"
            onClick={run}
            disabled={running || !a || !b}
          >
            {running ? <Spinner /> : <Sparkles size={16} />}
            {running ? 'Analysing…' : 'Explain the change'}
          </Button>
        </div>
        <ErrorBox message={runErr} />
      </Card>

      {varLoading && !variance && (
        <div className="py-10 text-center text-sm text-slate-500">Computing variances…</div>
      )}
      <ErrorBox message={varErr} />

      {variance && (
        <>
          {/* KPI tiles */}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {variance.topline.map((m) => (
              <KpiTile key={m.metric} m={m} />
            ))}
          </div>

          {variance.behaviour_flags?.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {variance.behaviour_flags.map((f, i) => (
                <Pill key={i} tone="amber">
                  {f}
                </Pill>
              ))}
            </div>
          )}

          <div className="grid gap-5 xl:grid-cols-5">
            {/* Bridge */}
            <Card
              className="xl:col-span-3"
              title="P&L bridge"
              subtitle={`${periodLabel(a)} → ${periodLabel(b)} realized P&L, by driver`}
              icon={<GitCompare size={18} />}
            >
              <Bridge report={variance} />
            </Card>

            {/* Drivers */}
            <Card
              className="xl:col-span-2"
              title="Drivers"
              subtitle="Ranked by contribution to the change"
            >
              <div className="max-h-[19rem] space-y-2.5 overflow-y-auto pr-1">
                {variance.drivers.length === 0 && (
                  <p className="text-sm text-slate-500">No drivers computed.</p>
                )}
                {variance.drivers.map((d, i) => (
                  <div key={i} className="rounded-xl border border-slate-800 bg-slate-950/40 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <Pill tone={DIM_TONE[d.dimension] ?? 'slate'}>{d.dimension}</Pill>
                        <span className="font-semibold text-slate-100">{d.key}</span>
                      </div>
                      <span
                        className={
                          d.delta >= 0
                            ? 'text-sm font-bold text-emerald-400 tabular-nums'
                            : 'text-sm font-bold text-rose-400 tabular-nums'
                        }
                      >
                        {signedUsd(d.delta)}
                      </span>
                    </div>
                    <div className="mt-1.5 text-xs text-slate-500">
                      {pct(d.contribution_pct, 0)} of the change
                      {d.note ? ` · ${d.note}` : ''}
                    </div>
                    {d.evidence_lot_ids?.length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {d.evidence_lot_ids.slice(0, 4).map((l) => (
                          <span
                            key={l}
                            title={l}
                            className="rounded bg-slate-800/80 px-1.5 py-0.5 font-mono text-[10px] text-slate-500"
                          >
                            {l.length > 14 ? l.slice(0, 13) + '…' : l}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </Card>
          </div>
        </>
      )}

      {(running || events.length > 0) && (
        <AgentTrace
          events={events}
          running={running}
          prismSession={runMeta?.prismSession}
          prismUrl={runMeta?.prismUrl}
        />
      )}

      {report && runMeta && (
        <ReportView
          report={report}
          model={runMeta.model}
          latencyMs={runMeta.latency}
          fallback={runMeta.fallback}
          prismSession={runMeta.prismSession}
          prismUrl={runMeta.prismUrl}
          runId={runMeta.runId}
          ibkrReady={ibkrReady}
        />
      )}
    </div>
  );
}
