import clsx from 'clsx';
import type { ReactNode } from 'react';

export type Tone = 'slate' | 'emerald' | 'rose' | 'amber' | 'sky' | 'violet';

const TONES: Record<Tone, string> = {
  slate: 'bg-slate-800/70 text-slate-300 ring-slate-700',
  emerald: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30',
  rose: 'bg-rose-500/10 text-rose-300 ring-rose-500/30',
  amber: 'bg-amber-500/10 text-amber-300 ring-amber-500/30',
  sky: 'bg-sky-500/10 text-sky-300 ring-sky-500/30',
  violet: 'bg-violet-500/10 text-violet-300 ring-violet-500/30',
};

export function Pill({
  tone = 'slate',
  children,
  title,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 whitespace-nowrap',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function StatusPill({
  label,
  value,
  tone = 'slate',
  title,
}: {
  label: string;
  value: string;
  tone?: Tone;
  title?: string;
}) {
  return (
    <Pill tone={tone} title={title}>
      <span
        className={clsx(
          'h-1.5 w-1.5 rounded-full',
          tone === 'emerald' && 'bg-emerald-400',
          tone === 'rose' && 'bg-rose-400',
          tone === 'amber' && 'bg-amber-400',
          tone === 'sky' && 'bg-sky-400',
          tone === 'violet' && 'bg-violet-400',
          tone === 'slate' && 'bg-slate-500',
        )}
      />
      <span className="text-slate-400">{label}</span>
      <span className="font-semibold">{value}</span>
    </Pill>
  );
}

export function Card({
  title,
  subtitle,
  icon,
  children,
  className,
  actions,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  children?: ReactNode;
  className?: string;
  actions?: ReactNode;
}) {
  return (
    <section
      className={clsx(
        'rounded-2xl border border-slate-800 bg-slate-900/50 p-5 shadow-lg shadow-black/20',
        className,
      )}
    >
      {(title || actions) && (
        <header className="mb-4 flex items-start justify-between gap-3">
          <div className="flex items-start gap-2.5">
            {icon && <span className="mt-0.5 text-slate-400">{icon}</span>}
            <div>
              {title && <h3 className="text-base font-semibold text-slate-100">{title}</h3>}
              {subtitle && <p className="mt-0.5 text-sm text-slate-400">{subtitle}</p>}
            </div>
          </div>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Button({
  variant = 'default',
  className,
  children,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'default' | 'danger' | 'ghost';
}) {
  return (
    <button
      {...rest}
      className={clsx(
        'inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-semibold transition',
        'disabled:cursor-not-allowed disabled:opacity-40',
        variant === 'primary' &&
          'bg-sky-500 text-slate-950 hover:bg-sky-400 shadow-lg shadow-sky-500/20',
        variant === 'default' &&
          'bg-slate-800 text-slate-100 hover:bg-slate-700 ring-1 ring-slate-700',
        variant === 'danger' &&
          'bg-rose-500/10 text-rose-300 ring-1 ring-rose-500/40 hover:bg-rose-500/20',
        variant === 'ghost' && 'text-slate-300 hover:bg-slate-800/70',
        className,
      )}
    >
      {children}
    </button>
  );
}

export function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={clsx('block', className)}>
      <span className="mb-1 block text-xs font-medium tracking-wide text-slate-400 uppercase">
        {label}
      </span>
      {children}
    </label>
  );
}

export const inputCls =
  'w-full rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-sm text-slate-100 ' +
  'placeholder:text-slate-600 outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20';

export function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
      {message}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={clsx(
        'inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent',
        className,
      )}
    />
  );
}

export function EvidenceChips({ items, max = 6 }: { items: string[]; max?: number }) {
  if (!items?.length) return null;
  const shown = items.slice(0, max);
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {shown.map((e, i) =>
        /^https?:\/\//.test(e) ? (
          <a
            key={i}
            href={e}
            target="_blank"
            rel="noreferrer"
            className="rounded bg-sky-500/10 px-1.5 py-0.5 font-mono text-[11px] text-sky-300 ring-1 ring-sky-500/20 hover:bg-sky-500/20"
          >
            {new URL(e).hostname.replace(/^www\./, '')}
          </a>
        ) : (
          <span
            key={i}
            title={e}
            className="rounded bg-slate-800/80 px-1.5 py-0.5 font-mono text-[11px] text-slate-400 ring-1 ring-slate-700"
          >
            {e.length > 22 ? e.slice(0, 21) + '…' : e}
          </span>
        ),
      )}
      {items.length > max && (
        <span className="px-1 py-0.5 text-[11px] text-slate-500">+{items.length - max} more</span>
      )}
    </div>
  );
}
