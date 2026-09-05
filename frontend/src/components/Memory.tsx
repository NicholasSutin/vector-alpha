import { useEffect, useState } from 'react';
import clsx from 'clsx';
import {
  Activity,
  Brain,
  CheckCheck,
  ExternalLink,
  History,
  Lightbulb,
  RefreshCw,
  XCircle,
} from 'lucide-react';
import type { AgentEvent, Insight, PrismStatus, Report, RunSummary } from '../api';
import { getInsights, getPrismStatus, getRun, getRuns, patchInsight } from '../api';
import { Button, Card, ErrorBox, Pill, Spinner } from './Pills';
import { AgentTrace } from './AgentTrace';
import { ReportView } from './ReportView';
import { dateTime, ms, periodLabel } from '../lib/format';
import type { Tone } from './Pills';

const STATUS_TONE: Record<string, Tone> = {
  open: 'slate',
  followed: 'sky',
  ignored: 'amber',
  validated: 'emerald',
  invalidated: 'rose',
};

const KIND_TONE: Record<string, Tone> = {
  observation: 'slate',
  advisement: 'sky',
  prediction: 'violet',
};

export function Memory({
  ibkrReady,
  reloadToken,
}: {
  ibkrReady: boolean;
  reloadToken: number;
}) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [insights, setInsights] = useState<Insight[]>([]);
  const [prism, setPrism] = useState<PrismStatus | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<{
    run: RunSummary;
    report: Report | null;
    events: AgentEvent[];
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    const results = await Promise.allSettled([getRuns(), getInsights(), getPrismStatus()]);
    if (results[0].status === 'fulfilled') setRuns(results[0].value.runs ?? []);
    else setError(results[0].reason?.message ?? 'Failed to load runs');
    if (results[1].status === 'fulfilled') setInsights(results[1].value.insights ?? []);
    if (results[2].status === 'fulfilled') setPrism(results[2].value);
    setLoading(false);
  };

  useEffect(() => {
    load();
  }, [reloadToken]); // eslint-disable-line react-hooks/exhaustive-deps

  const openRun = async (id: string) => {
    setSelected(id);
    setDetailLoading(true);
    try {
      setDetail(await getRun(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const mark = async (id: string, status: string) => {
    const prev = insights;
    setInsights((cur) => cur.map((i) => (i.id === id ? { ...i, status } : i)));
    try {
      const updated = await patchInsight(id, status);
      setInsights((cur) => cur.map((i) => (i.id === id ? updated : i)));
    } catch {
      setInsights(prev);
    }
  };

  const prismTone: Tone = prism?.live_connected
    ? 'emerald'
    : prism?.credential_ok
      ? 'amber'
      : prism?.configured
        ? 'amber'
        : 'slate';

  return (
    <div className="space-y-5">
      {/* PRISM card */}
      <Card
        title="PRISM observability"
        subtitle="Observe → Improve → Prove. Every agent run is one trajectory."
        icon={<Brain size={18} className="text-violet-400" />}
        actions={
          <button onClick={load} className="text-slate-500 hover:text-slate-300" title="Refresh">
            <RefreshCw size={14} />
          </button>
        }
      >
        <div className="flex flex-wrap items-center gap-2">
          <Pill tone={prismTone}>
            {prism?.live_connected
              ? 'live'
              : prism?.configured
                ? 'configured'
                : 'not configured'}
          </Pill>
          <Pill tone={prism?.credential_ok ? 'emerald' : 'slate'}>
            credential {prism?.credential_ok ? 'ok' : 'unverified'}
          </Pill>
          <Pill tone="slate">
            {prism?.live_trace_count ?? 0} live traces
          </Pill>
          {prism?.project_id && <Pill tone="violet">project {prism.project_id}</Pill>}
          {prism?.blocked_step && <Pill tone="rose">blocked: {prism.blocked_step}</Pill>}
        </div>
        {prism?.dashboard_url && (
          <a
            href={prism.dashboard_url}
            target="_blank"
            rel="noreferrer"
            className="mt-3 inline-flex items-center gap-1.5 text-sm font-semibold text-violet-400 hover:text-violet-300"
          >
            <ExternalLink size={14} /> Open PRISM dashboard
          </a>
        )}
        {prism?.host && (
          <p className="mt-2 font-mono text-xs text-slate-600">{prism.host}</p>
        )}
      </Card>

      <ErrorBox message={error} />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        {/* Runs */}
        <Card title="Runs" subtitle="Every explanation the agent has produced" icon={<History size={18} />}>
          {loading && <div className="py-6 text-center text-sm text-slate-500">Loading…</div>}
          {!loading && runs.length === 0 && (
            <p className="py-6 text-center text-sm text-slate-500">
              No runs yet. Explain a change to create one.
            </p>
          )}
          <ul className="max-h-[28rem] space-y-2 overflow-y-auto pr-1">
            {runs.map((r) => (
              <li key={r.id}>
                <button
                  onClick={() => openRun(r.id)}
                  className={clsx(
                    'w-full rounded-xl border p-3.5 text-left transition',
                    selected === r.id
                      ? 'border-sky-500/50 bg-sky-500/5'
                      : 'border-slate-800 bg-slate-950/40 hover:border-slate-700',
                  )}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill tone="sky">
                      {periodLabel(r.period_a)} → {periodLabel(r.period_b)}
                    </Pill>
                    <Pill
                      tone={
                        r.status === 'done' || r.status === 'complete'
                          ? 'emerald'
                          : r.status === 'error'
                            ? 'rose'
                            : 'amber'
                      }
                    >
                      {r.status}
                    </Pill>
                    {r.model && <Pill tone="violet">{r.model}</Pill>}
                    {r.latency_ms != null && <Pill tone="slate">{ms(r.latency_ms)}</Pill>}
                  </div>
                  <p className="mt-2 line-clamp-2 text-sm text-slate-200">
                    {r.headline || 'No headline'}
                  </p>
                  <p className="mt-1 text-xs text-slate-600">{dateTime(r.created_at)}</p>
                </button>
              </li>
            ))}
          </ul>
        </Card>

        {/* Insights */}
        <Card
          title="Insight memory"
          subtitle="What the agent remembers between runs"
          icon={<Lightbulb size={18} />}
        >
          {!loading && insights.length === 0 && (
            <p className="py-6 text-center text-sm text-slate-500">No insights saved yet.</p>
          )}
          <ul className="max-h-[28rem] space-y-2 overflow-y-auto pr-1">
            {insights.map((i) => (
              <li key={i.id} className="rounded-xl border border-slate-800 bg-slate-950/40 p-3.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Pill tone={KIND_TONE[i.kind] ?? 'slate'}>{i.kind}</Pill>
                  <Pill tone={STATUS_TONE[i.status] ?? 'slate'}>{i.status}</Pill>
                  <span className="ml-auto text-[11px] text-slate-600">
                    {dateTime(i.created_at)}
                  </span>
                </div>
                <p className="mt-2 text-sm leading-relaxed text-slate-200">{i.text}</p>
                {i.evidence?.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {i.evidence.slice(0, 4).map((e, k) => (
                      <span
                        key={k}
                        title={e}
                        className="rounded bg-slate-800/80 px-1.5 py-0.5 font-mono text-[10px] text-slate-500"
                      >
                        {e.length > 18 ? e.slice(0, 17) + '…' : e}
                      </span>
                    ))}
                  </div>
                )}
                <div className="mt-2.5 flex gap-2">
                  <Button
                    variant="ghost"
                    className="!px-2 !py-1 text-xs"
                    onClick={() => mark(i.id, 'followed')}
                    disabled={i.status === 'followed'}
                  >
                    <CheckCheck size={12} /> Followed
                  </Button>
                  <Button
                    variant="ghost"
                    className="!px-2 !py-1 text-xs"
                    onClick={() => mark(i.id, 'ignored')}
                    disabled={i.status === 'ignored'}
                  >
                    <XCircle size={12} /> Ignored
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      {/* Run detail */}
      {detailLoading && (
        <div className="flex items-center justify-center gap-2 py-8 text-sm text-slate-500">
          <Spinner /> Loading run…
        </div>
      )}
      {detail && !detailLoading && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-sm text-slate-400">
            <Activity size={14} />
            Run {detail.run.id.slice(0, 8)} · {periodLabel(detail.run.period_a)} →{' '}
            {periodLabel(detail.run.period_b)}
          </div>
          {detail.report && (
            <ReportView
              report={detail.report}
              model={detail.run.model}
              latencyMs={detail.run.latency_ms}
              fallback={detail.run.model === 'fallback'}
              prismSession={detail.run.prism_session_id}
              runId={detail.run.id}
              ibkrReady={ibkrReady}
            />
          )}
          {detail.events?.length > 0 && (
            <AgentTrace
              events={detail.events}
              running={false}
              prismSession={detail.run.prism_session_id}
            />
          )}
        </div>
      )}
    </div>
  );
}
