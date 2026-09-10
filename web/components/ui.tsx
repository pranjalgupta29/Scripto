"use client";

import type { CoverageDetail, CoverageMode } from "@/lib/types";

export function Button({
  children,
  variant = "primary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger";
}) {
  const styles = {
    primary: "bg-ink text-white hover:bg-black disabled:bg-black/30",
    ghost: "border border-black/15 hover:bg-black/5 disabled:opacity-40",
    danger: "text-red-700 hover:bg-red-50 disabled:opacity-40",
  }[variant];

  return (
    <button
      {...props}
      className={`rounded-md px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded-md border border-black/15 bg-white px-3 py-2 text-sm outline-none focus:border-ink ${props.className ?? ""}`}
    />
  );
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={`w-full rounded-md border border-black/15 bg-white px-3 py-2 text-sm outline-none focus:border-ink ${props.className ?? ""}`}
    />
  );
}

export function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-lg border border-black/10 bg-white p-5 ${className}`}>
      {children}
    </div>
  );
}

export function StatusPill({ status }: { status: string }) {
  const tone: Record<string, string> = {
    parsed: "bg-emerald-50 text-emerald-800 border-emerald-200",
    fetched: "bg-sky-50 text-sky-800 border-sky-200",
    pending: "bg-amber-50 text-amber-800 border-amber-200",
    failed: "bg-red-50 text-red-800 border-red-200",
  };
  return (
    <span
      className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${tone[status] ?? "border-black/15 bg-black/5"}`}
    >
      {status}
    </span>
  );
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
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm">
      <p className="font-semibold text-amber-900">
        {copy} This dossier is incomplete — treat guest specifics with care.
      </p>
      <p className="mt-1 text-amber-900/80">
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
        <div className="mt-3">
          <p className="font-medium text-amber-900">
            Adding any of these would help most:
          </p>
          <ul className="mt-1 list-disc pl-5 text-amber-900/80">
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
        <li key={i} className="text-xs text-black/55">
          <span className="mr-1">↳</span>
          {c.source_url ? (
            <a
              href={c.source_url}
              target="_blank"
              rel="noreferrer"
              className="underline decoration-black/20 underline-offset-2 hover:text-black"
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
  if (!total) return null;
  const pct = Math.round((finished / total) * 100);
  return (
    <div className="flex items-center gap-3">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-black/10">
        <div
          className="h-full rounded-full bg-ink transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs tabular-nums text-black/50">
        {finished}/{total}
      </span>
    </div>
  );
}
