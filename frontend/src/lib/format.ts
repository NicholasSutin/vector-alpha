const MINUS = '−'; // true minus sign

export function usd(v: number | null | undefined, decimals = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const abs = Math.abs(v);
  const s = abs.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return (v < 0 ? MINUS : '') + '$' + s;
}

export function signedUsd(v: number | null | undefined, decimals = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (v === 0) return '$0';
  return (v > 0 ? '+' : '') + usd(v, decimals);
}

export function pct(v: number | null | undefined, decimals = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${(v * 100).toFixed(decimals)}%`;
}

/** For values that are already percentages expressed 0..1 (e.g. pct fields on topline). */
export function signedPct(v: number | null | undefined, decimals = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const s = (v * 100).toFixed(decimals);
  const n = Number(s);
  if (n === 0) return '0.0%';
  return (n > 0 ? '+' : MINUS) + Math.abs(n).toFixed(decimals) + '%';
}

export function int(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return Math.round(v).toLocaleString('en-US');
}

export function signedInt(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const r = Math.round(v);
  if (r === 0) return '0';
  return (r > 0 ? '+' : MINUS) + Math.abs(r).toLocaleString('en-US');
}

export function days(v: number | null | undefined, decimals = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${v.toFixed(decimals)}d`;
}

export function signedDays(v: number | null | undefined, decimals = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (Math.abs(v) < 0.05) return '0.0d';
  return (v > 0 ? '+' : MINUS) + Math.abs(v).toFixed(decimals) + 'd';
}

export type MetricFormat = 'usd' | 'int' | 'pct' | 'days';

export function formatValue(v: number, f: MetricFormat): string {
  switch (f) {
    case 'usd':
      return usd(v);
    case 'int':
      return int(v);
    case 'pct':
      return pct(v);
    case 'days':
      return days(v);
  }
}

export function formatDelta(v: number, f: MetricFormat): string {
  switch (f) {
    case 'usd':
      return signedUsd(v);
    case 'int':
      return signedInt(v);
    case 'pct':
      return signedPct(v);
    case 'days':
      return signedDays(v);
  }
}

/** "2026-07" → "Jul 2026" */
export function periodLabel(p: string | null | undefined): string {
  if (!p) return '—';
  const m = /^(\d{4})-(\d{2})$/.exec(p);
  if (!m) return p;
  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  const idx = Number(m[2]) - 1;
  return `${months[idx] ?? m[2]} ${m[1]}`;
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export function ms(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  if (v < 1000) return `${Math.round(v)}ms`;
  return `${(v / 1000).toFixed(1)}s`;
}

export function truncate(s: string, n = 60): string {
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

/**
 * Splits a trailing "[source: https://...]" tag off a bullet.
 */
export function splitSourceTag(text: string): { body: string; source: string | null } {
  const m = /\s*\[source:\s*(https?:\/\/[^\]\s]+)\s*\]\s*$/i.exec(text);
  if (!m) return { body: text, source: null };
  return { body: text.slice(0, m.index).trim(), source: m[1] };
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}
