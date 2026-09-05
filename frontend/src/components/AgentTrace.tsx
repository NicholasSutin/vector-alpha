import { useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Brain,
  CheckCircle2,
  ChevronDown,
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
  collapsible = false,
  defaultOpen = true,
}: {
  events: AgentEvent[];
  running: boolean;
  prismSession?: string | null;
  prismUrl?: string | null;
  /** Render a disclosure header the user can toggle. */
  collapsible?: boolean;
  defaultOpen?: boolean;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(defaultOpen);

  // Expanded while streaming; collapses itself once the run finishes.
  const wasRunning = useRef(running);
  useEffect(() => {
    if (running) setOpen(true);
    else if (wasRunning.current && collapsible) setOpen(false);
    wasRunning.current = running;
  }, [running, collapsible]);

  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [events.length, open]);

  const visible = events.filter((e) => e.type !== 'text');
  const toolCalls = visible.filter((e) => e.type === 'tool_call').length;

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-5">
      <header className={clsx('flex items-center justify-between gap-3', open ? 'mb-3' : 'mb-0')}>
        <button
          type="button"
          onClick={() => collapsible && setOpen((v) => !v)}
          disabled={!collapsible}
          aria-expanded={open}
          className={clsx(
            'flex items-center gap-2 rounded-lg text-left',
            collapsible && 'cursor-pointer hover:opacity-80',
          )}
        >
          <Brain size={16} className={clsx('text-violet-400', running && 'va-pulse')} />
          <h3 className="text-base font-semibold text-slate-100">Agent trace</h3>
          {running && <Spinner className="text-violet-400" />}
          {collapsible && (
            <>
              <span className="text-xs font-medium text-slate-500">
                {toolCalls} tool call{toolCalls === 1 ? '' : 's'}
              </span>
              <ChevronDown
                size={15}
                className={clsx(
                  'text-slate-500 transition-transform',
                  open ? 'rotate-180' : 'rotate-0',
                )}
              />
            </>
          )}
        </button>
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

      {open && visible.length === 0 && (
        <p className="py-6 text-center text-sm text-slate-500">
          {running ? 'Waiting for the first tool call…' : 'No trace events yet.'}
        </p>
      )}

      <ol
        className="relative max-h-[26rem] space-y-1 overflow-y-auto pr-1"
        hidden={!open}
      >
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
