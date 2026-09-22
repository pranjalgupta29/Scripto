"use client";

import { AlertTriangle, Inbox } from "lucide-react";

import type { CoverageDetail, CoverageMode } from "@/lib/types";

/**
 * One focus treatment reused by every interactive element (buttons, inputs,
 * chips, nav items) so keyboard focus is never a per-component guess --
 * outline-none always pairs with this, never with nothing.
 */
export const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

export function Button({
  children,
  variant = "primary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "accent" | "ghost" | "danger";
}) {
  const styles = {
    primary: "bg-ink text-paper hover:opacity-90 disabled:opacity-40",
    accent: "bg-accent text-accent-contrast hover:opacity-90 disabled:opacity-40",
    ghost:
      "border border-line text-ink hover:border-line-strong hover:bg-surface-muted disabled:opacity-40",
    danger: "text-status-danger hover:bg-status-danger-bg disabled:opacity-40",
  }[variant];

  return (
    <button
      {...props}
      className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-ui font-medium transition disabled:cursor-not-allowed ${styles} ${FOCUS_RING} ${className}`}
    >
      {children}
    </button>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded-md border border-line bg-surface px-3 py-2 text-body text-ink outline-none transition placeholder:text-ink-subtle focus:border-accent ${FOCUS_RING} ${props.className ?? ""}`}
    />
  );
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={`w-full rounded-md border border-line bg-surface px-3 py-2 text-body text-ink outline-none transition placeholder:text-ink-subtle focus:border-accent ${FOCUS_RING} ${props.className ?? ""}`}
    />
  );
}

type CardOwnProps = {
  children: React.ReactNode;
  className?: string;
  variant?: "default" | "muted" | "interactive";
  as?: "div" | "button";
};

export function Card({
  children,
  className = "",
  variant = "default",
  as = "div",
  ...props
}: CardOwnProps &
  Omit<React.HTMLAttributes<HTMLDivElement> & React.ButtonHTMLAttributes<HTMLButtonElement>, keyof CardOwnProps>) {
  const variants = {
    default: "border-line bg-surface",
    muted: "border-line bg-surface-muted",
    interactive:
      "border-line bg-surface text-left transition hover:border-line-strong hover:shadow-raised",
  }[variant];

  const Component = as as React.ElementType;

  return (
    <Component
      {...props}
      className={`rounded-lg border p-5 ${variants} ${FOCUS_RING} ${className}`}
    >
      {children}
    </Component>
  );
}

const STATUS_TONE = {
  success: "border-status-success-border bg-status-success-bg text-status-success",
  info: "border-status-info-border bg-status-info-bg text-status-info",
  warning: "border-status-warning-border bg-status-warning-bg text-status-warning",
  danger: "border-status-danger-border bg-status-danger-bg text-status-danger",
  neutral: "border-line bg-surface-muted text-ink-muted",
} as const;

export type Tone = keyof typeof STATUS_TONE;

/**
 * The single place every status/provenance color in the app is decided, so
 * "what does amber mean here" only has to be answered once.
 */
export function Badge({
  tone = "neutral",
  children,
  title,
  className = "",
}: {
  tone?: Tone;
  children: React.ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex shrink-0 items-center gap-1 rounded-sm border px-1.5 py-0.5 text-caption font-medium ${STATUS_TONE[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

const SOURCE_STATUS_TONE: Record<string, Tone> = {
  parsed: "success",
  fetched: "info",
  pending: "warning",
  failed: "danger",
};

export function StatusPill({ status }: { status: string }) {
  return (
    <Badge tone={SOURCE_STATUS_TONE[status] ?? "neutral"}>{status}</Badge>
  );
}

/** Small tone-colored dot, used by the workspace nav to hint section state. */
export function StatusDot({ tone = "neutral" }: { tone?: Tone }) {
  const dot = {
    success: "bg-status-success",
    info: "bg-status-info",
    warning: "bg-status-warning",
    danger: "bg-status-danger",
    neutral: "bg-ink-subtle",
  }[tone];
  return <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />;
}

/**
 * The UI must never present a thin result as though it were a full one.
 * This banner says what was found, what was missing, and what would help.
 */
export function CoverageBanner({
  mode,
  detail,
}: {
  mode: CoverageMode | null;
  detail: CoverageDetail | null;
}) {
  if (!mode || mode === "rich") return null;

  const copy =
    mode === "sparse"
      ? "We found very little public material on this guest."
      : "We found limited public material on this guest.";

  return (
    <div className="rounded-lg border border-status-warning-border bg-status-warning-bg p-4 text-body">
      <p className="flex items-start gap-2 font-semibold text-status-warning">
        <AlertTriangle size={15} className="mt-0.5 shrink-0" />
        <span>
          {copy} This dossier is incomplete — treat guest specifics with care.
        </span>
      </p>
      <p className="mt-1 pl-[23px] text-ui text-status-warning">
        Built from {detail?.sources_parsed ?? 0} usable source
        {detail?.sources_parsed === 1 ? "" : "s"}
        {detail?.sources_failed ? `, ${detail.sources_failed} failed` : ""}
        {detail?.claim_count != null ? ` · ${detail.claim_count} claims` : ""}
        {detail?.has_long_form === false
          ? " · no long-form appearance found"
          : ""}
        .
      </p>
      {detail?.missing?.length ? (
        <div className="mt-3 pl-[23px]">
          <p className="text-ui font-medium text-status-warning">
            Adding any of these would help most:
          </p>
          <ul className="mt-1 list-disc pl-5 text-ui text-status-warning">
            {detail.missing.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function CitationList({
  citations,
}: {
  citations: { source_title: string | null; source_url: string | null; quote: string }[];
}) {
  if (!citations.length) return null;
  return (
    <ul className="mt-2 space-y-1">
      {citations.map((c, i) => (
        <li key={i} className="text-caption text-ink-subtle">
          <span className="mr-1">↳</span>
          {c.source_url ? (
            <a
              href={c.source_url}
              target="_blank"
              rel="noreferrer"
              className="underline decoration-ink-subtle underline-offset-2 hover:text-ink"
            >
              {c.source_title ?? c.source_url}
            </a>
          ) : (
            <span>{c.source_title ?? "pasted source"}</span>
          )}
          <span className="ml-1 italic">“{c.quote.slice(0, 160).trim()}”</span>
        </li>
      ))}
    </ul>
  );
}

export function ProgressBar({
  finished,
  total,
}: {
  finished: number;
  total: number;
}) {
  if (!total) {
    return (
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-muted">
        <div className="h-full w-1/3 animate-pulse rounded-full bg-accent/60" />
      </div>
    );
  }
  const pct = Math.round((finished / total) * 100);
  return (
    <div className="flex items-center gap-3">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-muted">
        <div
          className="h-full rounded-full bg-accent transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-caption tabular-nums text-ink-subtle">
        {finished}/{total}
      </span>
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-md border border-dashed border-line px-4 py-8 text-center">
      <Inbox size={18} className="text-ink-subtle" />
      <p className="text-body text-ink-muted">{title}</p>
      {hint ? <p className="text-caption text-ink-subtle">{hint}</p> : null}
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-md bg-surface-muted ${className}`}
    />
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-line border-t-ink-muted ${className}`}
    />
  );
}
