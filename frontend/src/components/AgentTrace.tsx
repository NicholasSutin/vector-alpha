import { useEffect, useRef } from 'react';
import clsx from 'clsx';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Brain,
  CheckCircle2,
  Database,
  Globe,
  Lightbulb,
  Send,
  Wrench,
} from 'lucide-react';
import type { AgentEvent } from '../api';
import { Spinner } from './Pills';

function toolIcon(name: string) {
  const n = name.toLowerCase();
  if (n.includes('search') || n.includes('web')) return <Globe size={14} />;
  if (n.includes('insight') || n.includes('recall') || n.includes('memory'))
    return <Lightbulb size={14} />;
  if (n.includes('compare') || n.includes('variance') || n.includes('period'))
    return <BarChart3 size={14} />;
  if (n.includes('transaction') || n.includes('drill') || n.includes('position'))
    return <Database size={14} />;
  if (n.includes('report') || n.includes('submit')) return <Send size={14} />;
  if (n.includes('trade') || n.includes('propose')) return <Activity size={14} />;
  return <Wrench size={14} />;
}

function summarize(input: unknown): string {
  if (input === null || input === undefined) return '';
  if (typeof input === 'string') return input;
  try {
    const s = JSON.stringify(input);
    return s.length > 120 ? s.slice(0, 119) + '…' : s;
  } catch {
    return '';
  }
}

export function AgentTrace({
  events,
  running,
  prismSession,
  prismUrl,
}: {
  events: AgentEvent[];
  running: boolean;
  prismSession?: string | null;
  prismUrl?: string | null;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [events.length]);

  const visible = events.filter((e) => e.type !== 'text');

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-5">
      <header className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Brain size={16} className={clsx('text-violet-400', running && 'va-pulse')} />
          <h3 className="text-base font-semibold text-slate-100">Agent trace</h3>
          {running && <Spinner className="text-violet-400" />}
        </div>
        {prismSession && (
          <a
            href={prismUrl || '#'}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => {
              if (!prismUrl) e.preventDefault();
            }}
            className="rounded-full bg-violet-500/10 px-2.5 py-1 font-mono text-[11px] text-violet-300 ring-1 ring-violet-500/30 hover:bg-violet-500/20"
            title="This run is traced end-to-end in PRISM"
          >
            traced to PRISM · {prismSession.slice(0, 8)}
          </a>
        )}
      </header>

      {visible.length === 0 && (
        <p className="py-6 text-center text-sm text-slate-500">
          {running ? 'Waiting for the first tool call…' : 'No trace events yet.'}
        </p>
      )}

      <ol className="relative max-h-[26rem] space-y-1 overflow-y-auto pr-1">
        {visible.map((e, i) => {
          if (e.type === 'status') {
            return (
              <li key={i} className="va-in flex items-start gap-2.5 py-1.5">
                <span className="mt-0.5 text-sky-400">
                  <Activity size={14} />
                </span>
                <span className="text-sm text-slate-300">{e.message}</span>
              </li>
            );
          }
          if (e.type === 'tool_call') {
            return (
              <li key={i} className="va-in flex items-start gap-2.5 py-1.5">
                <span className="mt-0.5 text-amber-400">{toolIcon(e.name)}</span>
                <div className="min-w-0">
                  <span className="font-mono text-sm font-semibold text-amber-200">{e.name}</span>
                  <span className="ml-2 font-mono text-xs break-all text-slate-500">
                    {summarize(e.input)}
                  </span>
                </div>
              </li>
            );
          }
          if (e.type === 'tool_result') {
            return (
              <li key={i} className="va-in flex items-start gap-2.5 border-l border-slate-800 py-1.5 pl-4 ml-1.5">
                <span className="mt-0.5 text-emerald-400">
                  <CheckCircle2 size={14} />
                </span>
                <span className="text-xs text-slate-400">{e.summary}</span>
              </li>
            );
          }
          if (e.type === 'error') {
            return (
              <li key={i} className="va-in flex items-start gap-2.5 py-1.5">
                <span className="mt-0.5 text-rose-400">
                  <AlertTriangle size={14} />
                </span>
                <span className="text-sm text-rose-300">{e.message}</span>
              </li>
            );
          }
          if (e.type === 'final') {
            return (
              <li key={i} className="va-in flex items-start gap-2.5 py-1.5">
                <span className="mt-0.5 text-emerald-400">
                  <CheckCircle2 size={14} />
                </span>
                <span className="text-sm font-semibold text-emerald-300">
                  Report ready · {e.model} · {Math.round(e.latency_ms)}ms
                </span>
              </li>
            );
          }
          return null;
        })}
        <div ref={endRef} />
      </ol>
    </div>
  );
}
