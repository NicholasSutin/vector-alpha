import { useCallback, useEffect, useState } from 'react';
import clsx from 'clsx';
import { Activity, Brain, LineChart, Plug, Sparkles } from 'lucide-react';
import type { Health, IbkrStatus, Overview, PrismStatus } from './api';
import { getHealth, getIbkrStatus, getOverview, getPrismStatus } from './api';
import { Connect } from './components/Connect';
import { Explain } from './components/Explain';
import { Memory } from './components/Memory';
import { TradeDesk } from './components/TradeDesk';
import { StatusPill } from './components/Pills';
import type { Tone } from './components/Pills';
import { int } from './lib/format';

type Tab = 'connect' | 'explain' | 'desk' | 'memory';

const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: 'connect', label: 'Connect', icon: <Plug size={15} /> },
  { id: 'explain', label: 'Explain the Change', icon: <Sparkles size={15} /> },
  { id: 'desk', label: 'Trade Desk', icon: <LineChart size={15} /> },
  { id: 'memory', label: 'Memory & Runs', icon: <Brain size={15} /> },
];

const TAB_KEY = 'vector-alpha:tab';

function readTab(): Tab | null {
  try {
    const v = localStorage.getItem(TAB_KEY);
    if (v === 'connect' || v === 'explain' || v === 'desk' || v === 'memory') return v;
  } catch {
    /* storage unavailable */
  }
  return null;
}

function writeTab(t: Tab) {
  try {
    localStorage.setItem(TAB_KEY, t);
  } catch {
    /* storage unavailable */
  }
}

export default function App() {
  const [tab, setTab] = useState<Tab>(() => readTab() ?? 'connect');
  const [health, setHealth] = useState<Health | null>(null);
  const [prism, setPrism] = useState<PrismStatus | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [ibkr, setIbkr] = useState<IbkrStatus | null>(null);
  const [booted, setBooted] = useState(false);
  const [offline, setOffline] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  const selectTab = (t: Tab) => {
    setTab(t);
    writeTab(t);
  };

  const refreshOverview = useCallback(async () => {
    try {
      const o = await getOverview();
      setOverview(o);
      setOffline(false);
    } catch {
      setOverview(null);
      setOffline(true);
    }
  }, []);

  const refreshIbkr = useCallback(async () => {
    try {
      setIbkr(await getIbkrStatus());
    } catch {
      setIbkr(null);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [h, p, o] = await Promise.allSettled([getHealth(), getPrismStatus(), getOverview()]);
      if (cancelled) return;
      if (h.status === 'fulfilled') setHealth(h.value);
      else setOffline(true);
      if (p.status === 'fulfilled') setPrism(p.value);
      if (o.status === 'fulfilled') {
        setOverview(o.value);
        // default to Explain when data already exists and the user hasn't chosen a tab
        if (o.value.has_data && readTab() === null) selectTab('explain');
      }
      setBooted(true);
    })();
    refreshIbkr();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const hasTavily = health?.has_tavily ?? health?.has_tavily_key ?? false;

  const hasLlm = health?.has_llm ?? health?.has_anthropic_key ?? false;
  const llmLabel = hasLlm ? `GIDE · ${health?.llm_model || health?.model || 'local'}` : 'deterministic';
  const llmTone: Tone = hasLlm ? 'violet' : 'amber';

  const prismLabel = prism?.live_connected
    ? 'live'
    : prism?.configured
      ? 'configured'
      : 'off';
  const prismTone: Tone = prism?.live_connected ? 'emerald' : prism?.configured ? 'amber' : 'slate';

  const ibkrLabel = ibkr?.authenticated
    ? ibkr.selected_account
      ? `${ibkr.selected_account}`
      : 'connected'
    : ibkr?.reachable
      ? 'gateway up'
      : 'off';
  const ibkrTone: Tone = ibkr?.authenticated ? 'emerald' : ibkr?.reachable ? 'amber' : 'slate';

  return (
    <div className="min-h-screen bg-[#0b0f17]">
      {/* Top bar */}
      <header className="sticky top-0 z-20 border-b border-slate-800/80 bg-[#0b0f17]/95 backdrop-blur">
        <div className="mx-auto max-w-[1400px] px-5 py-4">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
            <div className="flex items-baseline gap-3">
              <h1 className="text-xl font-bold tracking-tight text-slate-50">
                Vector<span className="text-sky-400"> Alpha</span>
              </h1>
              <p className="hidden text-sm text-slate-500 sm:block">
                Explain the change in your own portfolio
              </p>
            </div>
            <div className="ml-auto flex flex-wrap items-center gap-1.5">
              <StatusPill
                label="Data"
                value={overview?.has_data ? `${int(overview.transactions)} txns` : 'none'}
                tone={overview?.has_data ? 'emerald' : 'slate'}
                title={overview?.periods?.join(', ')}
              />
              <StatusPill label="LLM" value={llmLabel} tone={llmTone} />
              <StatusPill
                label="Macro"
                value={hasTavily ? 'Tavily' : 'off'}
                tone={hasTavily ? 'sky' : 'slate'}
                title={hasTavily ? 'Web research enabled via Tavily' : 'No Tavily key configured'}
              />
              <StatusPill
                label="PRISM"
                value={prismLabel}
                tone={prismTone}
                title={prism?.host}
              />
              <StatusPill label="IBKR" value={ibkrLabel} tone={ibkrTone} />
            </div>
          </div>

          {/* Tabs */}
          <nav className="mt-4 flex gap-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => selectTab(t.id)}
                className={clsx(
                  'flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-semibold transition',
                  tab === t.id
                    ? 'bg-slate-800 text-slate-50 ring-1 ring-slate-700'
                    : 'text-slate-500 hover:bg-slate-900 hover:text-slate-300',
                )}
              >
                {t.icon}
                <span className="hidden sm:inline">{t.label}</span>
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-5 py-6">
        {offline && (
          <div className="mb-5 flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
            <Activity size={15} />
            Backend unreachable at <span className="font-mono">localhost:8000</span> — start the API
            and reload.
          </div>
        )}

        {!booted && (
          <div className="py-24 text-center text-sm text-slate-500">Loading Vector Alpha…</div>
        )}

        {booted && tab === 'connect' && (
          <Connect
            overview={overview}
            refresh={refreshOverview}
            ibkr={ibkr}
            refreshIbkr={refreshIbkr}
          />
        )}
        {booted && tab === 'explain' && (
          <Explain
            overview={overview}
            ibkrReady={Boolean(ibkr?.authenticated)}
            onRunFinished={() => setReloadToken((n) => n + 1)}
          />
        )}
        {booted && tab === 'desk' && (
          <TradeDesk
            ibkr={ibkr}
            refreshIbkr={refreshIbkr}
            reloadToken={reloadToken}
            onGoConnect={() => selectTab('connect')}
          />
        )}
        {booted && tab === 'memory' && (
          <Memory ibkrReady={Boolean(ibkr?.authenticated)} reloadToken={reloadToken} />
        )}
      </main>

      <footer className="mx-auto max-w-[1400px] px-5 pb-10 text-xs text-slate-700">
        Vector Alpha · MONEY TALKS · Money Operations — “Explain the Change”
        {health?.version ? ` · api ${health.version}` : ''}
      </footer>
    </div>
  );
}
