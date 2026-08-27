"use client";

/**
 * Shared presentational primitives.
 *
 * `Why` is the important one. Spec §55 requires that every significant result
 * carries its reasoning, and the reliable way to enforce that is to make the
 * explanation part of the same component as the figure — so that rendering the
 * number without the evidence is not the easy path.
 */

import { useId, useState } from "react";

export function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <section className={`card ${className}`}>{children}</section>;
}

export function SectionHeading({
  title,
  hint,
  right,
}: {
  title: string;
  hint?: string;
  right?: React.ReactNode;
}) {
  return (
    <header className="mb-4 flex items-start justify-between gap-4">
      <div>
        <h2 className="text-base font-semibold text-ink">{title}</h2>
        {hint ? <p className="mt-0.5 text-xs text-ink-muted">{hint}</p> : null}
      </div>
      {right}
    </header>
  );
}

export function Badge({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <span className={`badge ${className}`}>{children}</span>;
}

export function Stat({
  label,
  value,
  sub,
  emphasis = false,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  emphasis?: boolean;
}) {
  return (
    <div>
      <div className="stat-label">{label}</div>
      <div className={emphasis ? "mt-1 text-figure-xl font-semibold tnum text-ink" : "stat-value"}>
        {value}
      </div>
      {sub ? <div className="mt-1 text-xs text-ink-muted">{sub}</div> : null}
    </div>
  );
}

/**
 * A disclosure holding the evidence behind a result.
 *
 * Collapsed by default so the summary stays scannable, but always present and
 * always reachable by keyboard. The count in the trigger tells the reader
 * there is something worth opening.
 */
export function Why({
  children,
  label = "Why?",
  count,
  defaultOpen = false,
}: {
  children: React.ReactNode;
  label?: string;
  count?: number;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();

  return (
    <div className="mt-3">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center gap-1.5 rounded text-xs font-medium
                   text-accent hover:underline"
      >
        <span
          aria-hidden
          className={`inline-block transition-transform ${open ? "rotate-90" : ""}`}
        >
          ▸
        </span>
        {label}
        {count !== undefined ? (
          <span className="text-ink-faint">({count})</span>
        ) : null}
      </button>
      <div id={id} hidden={!open} className="mt-2">
        {children}
      </div>
    </div>
  );
}

export function EvidenceList({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <ul className="space-y-1.5">
      {items.map((item, index) => (
        <li key={index} className="flex gap-2 text-sm leading-relaxed text-ink-soft">
          <span aria-hidden className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-ink-faint" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

export function Callout({
  tone = "neutral",
  title,
  children,
}: {
  tone?: "neutral" | "warning" | "info";
  title?: string;
  children: React.ReactNode;
}) {
  const tones = {
    neutral: "border-surface-border bg-surface-sunken",
    warning: "border-rating-high/30 bg-rating-high/5",
    info: "border-accent/25 bg-accent/5",
  } as const;

  return (
    <div className={`rounded-md border px-4 py-3 text-sm ${tones[tone]}`}>
      {title ? <div className="mb-1 font-medium text-ink">{title}</div> : null}
      <div className="text-ink-soft">{children}</div>
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div role="status" className="flex items-center gap-3 text-sm text-ink-muted">
      <span
        aria-hidden
        className="h-4 w-4 animate-spin rounded-full border-2 border-surface-border
                   border-t-accent"
      />
      {label}
    </div>
  );
}
