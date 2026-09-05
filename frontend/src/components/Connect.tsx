import { useEffect, useState } from 'react';
import {
  Building2,
  Database,
  ExternalLink,
  FileUp,
  RefreshCw,
  Rocket,
  Trash2,
} from 'lucide-react';
import type { IbkrStatus, IngestResult, Overview, RobinhoodStatus } from '../api';
import {
  getRobinhoodStatus,
  ibkrFlex,
  ingestCsv,
  ingestDemo,
  resetData,
  robinhoodLogin,
  selectIbkrAccount,
  syncIbkr,
  syncRobinhood,
} from '../api';
import { Button, Card, ErrorBox, Field, Pill, Spinner, inputCls } from './Pills';
import { int, shortDate } from '../lib/format';

function IngestSummary({ r }: { r: IngestResult | null }) {
  if (!r) return null;
  return (
    <div className="mt-3 rounded-xl border border-emerald-500/25 bg-emerald-500/5 p-3.5">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="text-2xl font-bold text-emerald-300 tabular-nums">
          {int(r.inserted)}
        </span>
        <span className="text-sm text-slate-400">
          transactions inserted from <span className="font-mono text-slate-300">{r.source}</span>
        </span>
        {r.skipped_duplicates > 0 && (
          <span className="text-xs text-slate-500">{int(r.skipped_duplicates)} duplicates skipped</span>
        )}
      </div>
      <div className="mt-2 text-sm text-slate-400">
        {shortDate(r.date_range?.start)} → {shortDate(r.date_range?.end)}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {Object.entries(r.asset_types ?? {}).map(([k, v]) => (
          <Pill key={k} tone="slate">
            {k} <span className="font-bold text-slate-200">{v}</span>
          </Pill>
        ))}
        {r.account_ids?.map((a) => (
          <Pill key={a} tone="sky">
            {a}
          </Pill>
        ))}
      </div>
      {r.warnings?.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {r.warnings.slice(0, 4).map((w, i) => (
            <li key={i} className="text-xs text-amber-300">
              ⚠ {w}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Connect({
  overview,
  refresh,
  ibkr,
  refreshIbkr,
  onDataLoaded,
}: {
  overview: Overview | null;
  refresh: () => Promise<void>;
  ibkr: IbkrStatus | null;
  refreshIbkr: () => Promise<void>;
  /** Called after a successful demo load so the app can jump to Explain. */
  onDataLoaded?: () => void;
}) {
  /* demo */
  const [demoBusy, setDemoBusy] = useState(false);
  const [demoResult, setDemoResult] = useState<IngestResult | null>(null);
  const [demoErr, setDemoErr] = useState<string | null>(null);

  /* csv */
  const [file, setFile] = useState<File | null>(null);
  const [csvSource, setCsvSource] = useState('auto');
  const [csvBusy, setCsvBusy] = useState(false);
  const [csvResult, setCsvResult] = useState<IngestResult | null>(null);
  const [csvErr, setCsvErr] = useState<string | null>(null);

  /* ibkr */
  const [ibkrBusy, setIbkrBusy] = useState(false);
  const [ibkrErr, setIbkrErr] = useState<string | null>(null);
  const [ibkrResult, setIbkrResult] = useState<IngestResult | null>(null);
  const [account, setAccount] = useState('');
  const [flexToken, setFlexToken] = useState('');
  const [flexQuery, setFlexQuery] = useState('');

  /* robinhood */
  const [rh, setRh] = useState<RobinhoodStatus | null>(null);
  const [rhUser, setRhUser] = useState('');
  const [rhPass, setRhPass] = useState('');
  const [rhMfa, setRhMfa] = useState('');
  const [rhChallenge, setRhChallenge] = useState<string | null>(null);
  const [rhBusy, setRhBusy] = useState(false);
  const [rhErr, setRhErr] = useState<string | null>(null);
  const [rhResult, setRhResult] = useState<IngestResult | null>(null);

  /* reset */
  const [resetBusy, setResetBusy] = useState(false);

  useEffect(() => {
    getRobinhoodStatus().then(setRh).catch(() => {});
  }, []);

  useEffect(() => {
    if (ibkr?.selected_account) setAccount(ibkr.selected_account);
    else if (ibkr?.accounts?.length && !account) setAccount(ibkr.accounts[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ibkr]);

  const err = (e: unknown) => (e instanceof Error ? e.message : String(e));

  const loadDemo = async () => {
    setDemoBusy(true);
    setDemoErr(null);
    try {
      setDemoResult(await ingestDemo());
      await refresh();
      // Straight to the story: the demo book exists to be explained.
      window.setTimeout(() => onDataLoaded?.(), 700);
    } catch (e) {
      setDemoErr(err(e));
    } finally {
      setDemoBusy(false);
    }
  };

  const uploadCsv = async () => {
    if (!file) return;
    setCsvBusy(true);
    setCsvErr(null);
    try {
      setCsvResult(await ingestCsv(file, csvSource));
      await refresh();
    } catch (e) {
      setCsvErr(err(e));
    } finally {
      setCsvBusy(false);
    }
  };

  const doSelectAccount = async (id: string) => {
    setAccount(id);
    try {
      await selectIbkrAccount(id);
      await refreshIbkr();
    } catch (e) {
      setIbkrErr(err(e));
    }
  };

  const doIbkrSync = async () => {
    setIbkrBusy(true);
    setIbkrErr(null);
    try {
      setIbkrResult(await syncIbkr());
      await refresh();
      await refreshIbkr();
    } catch (e) {
      setIbkrErr(err(e));
    } finally {
      setIbkrBusy(false);
    }
  };

  const doFlex = async () => {
    setIbkrBusy(true);
    setIbkrErr(null);
    try {
      setIbkrResult(await ibkrFlex(flexToken, flexQuery));
      await refresh();
    } catch (e) {
      setIbkrErr(err(e));
    } finally {
      setIbkrBusy(false);
    }
  };

  const doRhLogin = async () => {
    setRhBusy(true);
    setRhErr(null);
    try {
      const res = await robinhoodLogin(rhUser, rhPass, rhMfa);
      if (res.status === 'challenge') {
        setRhChallenge(res.message || 'Approve the login in your Robinhood app, then retry.');
      } else if (res.status === 'error') {
        setRhErr(res.message);
      } else {
        setRhChallenge(null);
        setRhErr(null);
      }
      setRh(await getRobinhoodStatus());
    } catch (e) {
      setRhErr(err(e));
    } finally {
      setRhBusy(false);
    }
  };

  const doRhSync = async () => {
    setRhBusy(true);
    setRhErr(null);
    try {
      setRhResult(await syncRobinhood());
      await refresh();
    } catch (e) {
      setRhErr(err(e));
    } finally {
      setRhBusy(false);
    }
  };

  const doReset = async () => {
    if (!window.confirm('Delete ALL transactions, snapshots, runs and insights?')) return;
    setResetBusy(true);
    try {
      await resetData();
      setDemoResult(null);
      setCsvResult(null);
      setIbkrResult(null);
      setRhResult(null);
      await refresh();
    } catch {
      /* ignore */
    } finally {
      setResetBusy(false);
    }
  };

  const ibkrTone = ibkr?.authenticated ? 'emerald' : ibkr?.reachable ? 'amber' : 'slate';

  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-2">
        {/* Demo */}
        <Card
          title="Load demo book"
          subtitle="A synthetic Jan–Aug 2026 options + equity book with a bad July. The fastest path to a demo."
          icon={<Rocket size={18} />}
        >
          <Button variant="primary" onClick={loadDemo} disabled={demoBusy}>
            {demoBusy ? <Spinner /> : <Rocket size={14} />}
            Load demo book
          </Button>
          <ErrorBox message={demoErr} />
          <IngestSummary r={demoResult} />
        </Card>

        {/* CSV */}
        <Card
          title="Upload broker CSV"
          subtitle="Robinhood activity export, IBKR Flex CSV, a monthly account-summary statement, or any generic ledger."
          icon={<FileUp size={18} />}
        >
          <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
            <Field label="File">
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="block w-full text-sm text-slate-400 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-800 file:px-3 file:py-2 file:text-sm file:font-semibold file:text-slate-200 hover:file:bg-slate-700"
              />
            </Field>
            <Field label="Source">
              <select
                value={csvSource}
                onChange={(e) => setCsvSource(e.target.value)}
                className={inputCls}
              >
                <option value="auto">auto-detect</option>
                <option value="robinhood">robinhood</option>
                <option value="ibkr_flex">ibkr_flex</option>
                <option value="generic">generic</option>
                <option value="account_summary">account_summary (monthly statement)</option>
              </select>
            </Field>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Accepts <span className="text-slate-400">Robinhood activity export</span>,{' '}
            <span className="text-slate-400">IBKR Flex query CSV</span>,{' '}
            <span className="text-slate-400">any generic ledger</span> (date, symbol, side, qty,
            price, amount), or <span className="text-slate-400">auto-detect</span>.
          </p>
          <Button className="mt-3" onClick={uploadCsv} disabled={!file || csvBusy}>
            {csvBusy ? <Spinner /> : <FileUp size={14} />}
            Upload &amp; ingest
          </Button>
          <ErrorBox message={csvErr} />
          <IngestSummary r={csvResult} />
        </Card>

        {/* IBKR */}
        <Card
          title="IBKR (paper)"
          subtitle="Client Portal Gateway on localhost. Paper accounts only (DU/DF)."
          icon={<Building2 size={18} />}
          actions={
            <button
              onClick={() => refreshIbkr()}
              className="text-slate-500 hover:text-slate-300"
              title="Refresh status"
            >
              <RefreshCw size={14} />
            </button>
          }
        >
          <div className="flex flex-wrap gap-1.5">
            <Pill tone={ibkr?.reachable ? 'emerald' : 'rose'}>
              {ibkr?.reachable ? 'gateway reachable' : 'gateway unreachable'}
            </Pill>
            <Pill tone={ibkrTone}>
              {ibkr?.authenticated ? 'authenticated' : 'not authenticated'}
            </Pill>
            {ibkr?.paper && <Pill tone="sky">PAPER</Pill>}
            {ibkr?.accounts?.length ? (
              <Pill tone="slate">{ibkr.accounts.length} account(s)</Pill>
            ) : null}
          </div>

          {ibkr?.login_url && !ibkr.authenticated && (
            <a
              href={ibkr.login_url}
              target="_blank"
              rel="noreferrer"
              className="mt-3 inline-flex items-center gap-1.5 text-sm font-semibold text-sky-400 hover:text-sky-300"
            >
              <ExternalLink size={14} /> Open gateway login
            </a>
          )}

          {ibkr?.accounts?.length ? (
            <Field label="Account" className="mt-3">
              <select
                value={account}
                onChange={(e) => doSelectAccount(e.target.value)}
                className={inputCls}
              >
                {ibkr.accounts.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}

          <Button className="mt-3" onClick={doIbkrSync} disabled={ibkrBusy || !ibkr?.authenticated}>
            {ibkrBusy ? <Spinner /> : <RefreshCw size={14} />}
            Sync positions &amp; recent trades
          </Button>

          <div className="mt-4 border-t border-slate-800 pt-3">
            <div className="mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">
              Flex statement (longer history)
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <input
                className={inputCls}
                placeholder="Flex token"
                value={flexToken}
                onChange={(e) => setFlexToken(e.target.value)}
              />
              <input
                className={inputCls}
                placeholder="Query id"
                value={flexQuery}
                onChange={(e) => setFlexQuery(e.target.value)}
              />
            </div>
            <Button
              className="mt-2"
              onClick={doFlex}
              disabled={ibkrBusy || !flexToken || !flexQuery}
            >
              Fetch Flex statement
            </Button>
          </div>

          <ErrorBox message={ibkrErr} />
          <IngestSummary r={ibkrResult} />
        </Card>

        {/* Robinhood */}
        <Card
          title="Robinhood live"
          subtitle="Sign in to pull orders, dividends and transfers directly."
          icon={<Database size={18} />}
        >
          <div className="flex flex-wrap gap-1.5">
            <Pill tone={rh?.logged_in ? 'emerald' : 'slate'}>
              {rh?.logged_in ? `logged in${rh.username ? ' · ' + rh.username : ''}` : 'signed out'}
            </Pill>
          </div>

          {!rh?.logged_in && (
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <input
                className={inputCls}
                placeholder="Username / email"
                autoComplete="off"
                value={rhUser}
                onChange={(e) => setRhUser(e.target.value)}
              />
              <input
                className={inputCls}
                type="password"
                placeholder="Password"
                autoComplete="off"
                value={rhPass}
                onChange={(e) => setRhPass(e.target.value)}
              />
              <input
                className={inputCls}
                placeholder="MFA / SMS code (optional)"
                value={rhMfa}
                onChange={(e) => setRhMfa(e.target.value)}
              />
            </div>
          )}

          {!rh?.logged_in && (
            <p className="mt-2 text-xs text-slate-500">
              Credentials are used only for this session to call Robinhood on your behalf — they are
              never stored.
            </p>
          )}

          {rhChallenge && (
            <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
              {rhChallenge}
            </div>
          )}

          <div className="mt-3 flex flex-wrap gap-2">
            {!rh?.logged_in && (
              <Button onClick={doRhLogin} disabled={rhBusy || !rhUser || !rhPass}>
                {rhBusy ? <Spinner /> : null}
                {rhChallenge ? 'I approved it / retry' : 'Sign in'}
              </Button>
            )}
            <Button variant="primary" onClick={doRhSync} disabled={rhBusy || !rh?.logged_in}>
              {rhBusy ? <Spinner /> : <RefreshCw size={14} />}
              Sync
            </Button>
          </div>

          <ErrorBox message={rhErr} />
          <IngestSummary r={rhResult} />
        </Card>
      </div>

      {/* Sources table */}
      <Card
        title="Loaded data"
        subtitle={
          overview?.has_data
            ? `${int(overview.transactions)} transactions across ${overview.periods.length} periods`
            : 'Nothing loaded yet.'
        }
        icon={<Database size={18} />}
        actions={
          <Button variant="danger" onClick={doReset} disabled={resetBusy}>
            {resetBusy ? <Spinner /> : <Trash2 size={14} />}
            Reset data
          </Button>
        }
      >
        {overview?.sources?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-xs tracking-wide text-slate-500 uppercase">
                  <th className="py-2 pr-4 font-medium">Source</th>
                  <th className="py-2 pr-4 font-medium">Account</th>
                  <th className="py-2 pr-4 text-right font-medium">Txns</th>
                  <th className="py-2 pr-4 font-medium">From</th>
                  <th className="py-2 font-medium">To</th>
                </tr>
              </thead>
              <tbody>
                {overview.sources.map((s, i) => (
                  <tr key={i} className="border-b border-slate-800/60 last:border-0">
                    <td className="py-2 pr-4 font-mono text-slate-200">{s.source}</td>
                    <td className="py-2 pr-4 font-mono text-slate-400">{s.account_id || '—'}</td>
                    <td className="py-2 pr-4 text-right text-slate-200 tabular-nums">
                      {int(s.count)}
                    </td>
                    <td className="py-2 pr-4 text-slate-400">{shortDate(s.start)}</td>
                    <td className="py-2 text-slate-400">{shortDate(s.end)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-4 text-sm text-slate-500">
            Load the demo book or connect a broker to get started.
          </p>
        )}
      </Card>
    </div>
  );
}
