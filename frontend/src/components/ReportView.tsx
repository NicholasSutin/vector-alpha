import { useMemo, useState } from 'react';
import clsx from 'clsx';
import {
  AlertTriangle,
  BookOpen,
  Brain,
  ExternalLink,
  Gauge,
  Globe2,
  History,
  Lightbulb,
  Newspaper,
  TrendingUp,
  Zap,
 Building2 } from 'lucide-react';
import type { Report } from '../api';
import { EvidenceChips, Pill } from './Pills';
import { Trades } from './Trades';
import { hostOf, ms, pct, splitSourceTag } from '../lib/format';

function Section({
  title,
  icon,
  children,
  className,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={clsx('rounded-2xl border border-slate-800 bg-slate-900/50 p-5', className)}>
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold tracking-wide text-slate-400 uppercase">
        <span className="text-slate-500">{icon}</span>
        {title}
      </h3>
      {children}
    </section>
  );
}

function Bullets({ items }: { items: string[] }) {
  return (
    <ul className="space-y-2">
      {items.map((t, i) => (
        <li key={i} className="flex gap-2.5 text-[15px] leading-relaxed text-slate-200">
          <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-600" />
          <span>{t}</span>
        </li>
      ))}
    </ul>
  );
}

const PRIORITY: Record<string, { ring: string; dot: string; label: string }> = {
  high: { ring: 'border-rose-500/40 bg-rose-500/5', dot: 'bg-rose-400', label: 'text-rose-300' },
  medium: {
    ring: 'border-amber-500/40 bg-amber-500/5',
    dot: 'bg-amber-400',
    label: 'text-amber-300',
  },
  low: { ring: 'border-sky-500/40 bg-sky-500/5', dot: 'bg-sky-400', label: 'text-sky-300' },
};

const VERDICT_TONE: Record<string, 'emerald' | 'rose' | 'amber' | 'sky' | 'slate'> = {
  validated: 'emerald',
  invalidated: 'rose',
  followed: 'sky',
  ignored: 'amber',
  unclear: 'slate',
};

/** Most decision-useful verdicts first. */
const VERDICT_RANK: Record<string, number> = {
  validated: 0,
  invalidated: 1,
  followed: 2,
  ignored: 3,
  unclear: 4,
};

const REVIEW_CAP = 8;
const SOURCE_CAP = 8;

function MoreLink({ open, onClick, label }: { open: boolean; onClick: () => void; label?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="mt-2 inline-flex rounded text-xs font-semibold text-sky-400 hover:text-sky-300"
    >
      {open ? 'Show less' : (label ?? 'Show more')}
    </button>
  );
}

/** Clamps long report text to 4 lines with a toggle. */
function ClampText({ text, className }: { text: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 220;
  return (
    <>
      <p className={clsx(className, !open && long && 'line-clamp-4')}>{text}</p>
      {long && <MoreLink open={open} onClick={() => setOpen((v) => !v)} label="more" />}
    </>
  );
}

function PriorInsightReview({ items }: { items: Report['prior_insight_review'] }) {
  const [showAll, setShowAll] = useState(false);
  const sorted = useMemo(
    () =>
      [...items].sort(
        (x, y) => (VERDICT_RANK[x.verdict] ?? 9) - (VERDICT_RANK[y.verdict] ?? 9),
      ),
    [items],
  );
  const shown = showAll ? sorted : sorted.slice(0, REVIEW_CAP);
  return (
    <>
      <div className="space-y-2.5">
        {shown.map((r, i) => (
          <div
            key={i}
            className="flex flex-col gap-1.5 rounded-xl border border-slate-800 bg-slate-950/40 p-3.5 sm:flex-row sm:items-start sm:gap-3"
          >
            <Pill tone={VERDICT_TONE[r.verdict] ?? 'slate'}>{r.verdict}</Pill>
            <div className="min-w-0 flex-1">
              <ClampText text={r.text} className="text-sm text-slate-200" />
              {r.note && <p className="mt-0.5 text-xs text-slate-500">{r.note}</p>}
            </div>
          </div>
        ))}
      </div>
      {sorted.length > REVIEW_CAP && (
        <MoreLink
          open={showAll}
          onClick={() => setShowAll((v) => !v)}
          label={`Show all (${sorted.length})`}
        />
      )}
    </>
  );
}

function SourcesList({ sources }: { sources: string[] }) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? sources : sources.slice(0, SOURCE_CAP);
  return (
    <>
      <ul className="space-y-1.5">
        {shown.map((s, i) => (
          <li key={i}>
            <a
              href={s}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded text-sm text-sky-400 hover:text-sky-300 hover:underline"
            >
              <ExternalLink size={12} />
              {hostOf(s)}
              <span className="text-slate-600">{s.length > 70 ? s.slice(0, 69) + '…' : s}</span>
            </a>
          </li>
        ))}
      </ul>
      {sources.length > SOURCE_CAP && (
        <MoreLink
          open={showAll}
          onClick={() => setShowAll((v) => !v)}
          label={`Show all (${sources.length})`}
        />
      )}
    </>
  );
}

export function ReportView({
  report,
  model,
  latencyMs,
  fallback,
  prismSession,
  prismUrl,
  runId,
  ibkrReady,
}: {
  report: Report;
  model?: string | null;
  latencyMs?: number | null;
  fallback?: boolean;
  prismSession?: string | null;
  prismUrl?: string | null;
  runId?: string;
  ibkrReady: boolean;
}) {
  const marketContext = report.market_context ?? [];
  const companyChanges = report.company_changes ?? [];
  const conf = Math.max(0, Math.min(1, report.confidence ?? 0));

  return (
    <div className="space-y-4">
      {/* Headline */}
      <div className="va-in rounded-2xl border border-sky-500/25 bg-gradient-to-br from-sky-500/10 to-slate-900/40 p-6">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Pill tone="sky">
            <Zap size={12} /> Explanation
          </Pill>
          {fallback ? (
            <Pill tone="amber" title="No Anthropic key — deterministic narrative from computed facts">
              deterministic mode
            </Pill>
          ) : (
            model && <Pill tone="violet">{model}</Pill>
          )}
          {latencyMs != null && <Pill tone="slate">{ms(latencyMs)}</Pill>}
          {prismSession && (
            <a href={prismUrl || '#'} target="_blank" rel="noreferrer" onClick={(e) => !prismUrl && e.preventDefault()}>
              <Pill tone="violet" className="hover:bg-violet-500/20">
                <Brain size={12} /> traced to PRISM
              </Pill>
            </a>
          )}
        </div>
        <h2 className="text-2xl leading-snug font-bold tracking-tight text-slate-50 sm:text-3xl">
          {report.headline}
        </h2>
        {/* confidence meter */}
        <div className="mt-4 flex items-center gap-3">
          <Gauge size={14} className="text-slate-500" />
          <div className="h-1.5 w-40 overflow-hidden rounded-full bg-slate-800">
            <div
              className={clsx(
                'h-full rounded-full',
                conf >= 0.7 ? 'bg-emerald-400' : conf >= 0.4 ? 'bg-amber-400' : 'bg-rose-400',
              )}
              style={{ width: `${conf * 100}%` }}
            />
          </div>
          <span className="text-xs font-semibold text-slate-400">
            {pct(conf, 0)} confidence
          </span>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {report.what_changed?.length > 0 && (
          <Section title="What changed" icon={<TrendingUp size={14} />}>
            <Bullets items={report.what_changed} />
          </Section>
        )}
        {report.why?.length > 0 && (
          <Section title="Why" icon={<BookOpen size={14} />}>
            <Bullets items={report.why} />
          </Section>
        )}
      </div>

      {companyChanges.length > 0 && (
        <Section title="What changed at the companies" icon={<Building2 size={14} />}>
          <ul className="grid gap-3 md:grid-cols-2">
            {companyChanges.map((c, i) => (
              <li
                key={i}
                className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-[15px] leading-relaxed text-slate-200"
              >
                <div className="mb-1 flex items-center gap-2">
                  <span className="rounded bg-sky-500/15 px-2 py-0.5 font-mono text-xs font-semibold text-sky-200 ring-1 ring-sky-500/30">
                    {c.symbol}
                  </span>
                </div>
                <ClampText text={c.text} />
                {c.sources?.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {c.sources.slice(0, 3).map((u, j) => (
                      <a
                        key={j}
                        href={u}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 rounded bg-sky-500/10 px-1.5 py-0.5 text-[11px] font-medium text-sky-300 ring-1 ring-sky-500/25 hover:bg-sky-500/20"
                      >
                        <Globe2 size={10} />
                        {hostOf(u)}
                      </a>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {marketContext.length > 0 && (
        <Section title="Market context" icon={<Newspaper size={14} />}>
          <ul className="space-y-2">
            {marketContext.map((raw, i) => {
              const { body, source } = splitSourceTag(raw);
              return (
                <li key={i} className="flex gap-2.5 text-[15px] leading-relaxed text-slate-200">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-sky-500/60" />
                  <span>
                    {body}{' '}
                    {source && (
                      <a
                        href={source}
                        target="_blank"
                        rel="noreferrer"
                        className="ml-1 inline-flex items-center gap-1 rounded bg-sky-500/10 px-1.5 py-0.5 align-middle text-[11px] font-medium text-sky-300 ring-1 ring-sky-500/25 hover:bg-sky-500/20"
                      >
                        <Globe2 size={10} />
                        {hostOf(source)}
                      </a>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        </Section>
      )}

      {report.drivers?.length > 0 && (
        <Section title="Drivers" icon={<TrendingUp size={14} />}>
          <div className="space-y-3">
            {report.drivers.map((d, i) => {
              const share = Math.min(1, Math.abs(d.contribution_pct ?? 0));
              const neg = (d.contribution_pct ?? 0) < 0;
              return (
                <div key={i} className="rounded-xl border border-slate-800 bg-slate-950/40 p-3.5">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="font-semibold text-slate-100">{d.name}</span>
                    <span
                      className={clsx(
                        'text-sm font-bold tabular-nums',
                        neg ? 'text-rose-400' : 'text-emerald-400',
                      )}
                    >
                      {pct(d.contribution_pct, 0)}
                    </span>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-800">
                    <div
                      className={clsx('h-full rounded-full', neg ? 'bg-rose-500' : 'bg-emerald-500')}
                      style={{ width: `${share * 100}%` }}
                    />
                  </div>
                  {d.detail && <p className="mt-2 text-sm text-slate-400">{d.detail}</p>}
                  <EvidenceChips items={d.evidence ?? []} />
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {report.behaviour?.length > 0 && (
        <Section title="Behaviour" icon={<AlertTriangle size={14} />}>
          <Bullets items={report.behaviour} />
        </Section>
      )}

      {report.advisements?.length > 0 && (
        <Section title="Advisements" icon={<Lightbulb size={14} />}>
          <div className="grid gap-3 md:grid-cols-2">
            {report.advisements.map((a, i) => {
              const p = PRIORITY[a.priority] ?? PRIORITY.low;
              return (
                <div key={i} className={clsx('rounded-xl border p-4', p.ring)}>
                  <div className="flex items-center gap-2">
                    <span className={clsx('h-2 w-2 rounded-full', p.dot)} />
                    <span
                      className={clsx(
                        'text-[10px] font-bold tracking-widest uppercase',
                        p.label,
                      )}
                    >
                      {a.priority}
                    </span>
                  </div>
                  <h4 className="mt-1.5 font-semibold text-slate-100">{a.title}</h4>
                  <p className="mt-1 text-sm leading-relaxed text-slate-400">{a.detail}</p>
                  <EvidenceChips items={a.evidence ?? []} />
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {report.prior_insight_review?.length > 0 && (
        <Section title="Prior insight review" icon={<History size={14} />}>
          <PriorInsightReview items={report.prior_insight_review} />
        </Section>
      )}

      {report.proposed_trades?.length > 0 && (
        <Section title="Proposed paper trades" icon={<Zap size={14} />}>
          <Trades trades={report.proposed_trades} runId={runId} ibkrReady={ibkrReady} />
        </Section>
      )}

      {report.sources?.length > 0 && (
        <Section title="Sources" icon={<Globe2 size={14} />}>
          <SourcesList sources={report.sources} />
        </Section>
      )}
    </div>
  );
}
