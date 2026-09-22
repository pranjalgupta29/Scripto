"use client";

import { FileText, ListChecks, Mic, ScrollText, type LucideIcon } from "lucide-react";

import type { Episode } from "@/lib/types";
import { FOCUS_RING, ProgressBar, StatusDot, type Tone } from "@/components/ui";

export type Section = "sources" | "dossier" | "topics" | "script";

const SECTIONS: Section[] = ["sources", "dossier", "topics", "script"];

const SECTION_META: Record<Section, { label: string; icon: LucideIcon }> = {
  sources: { label: "Sources", icon: ScrollText },
  dossier: { label: "Dossier", icon: FileText },
  topics: { label: "Topics", icon: ListChecks },
  script: { label: "Script", icon: Mic },
};

function sourcesTone(episode: Episode): Tone {
  if (episode.sources.some((s) => s.status === "failed")) return "warning";
  if (episode.sources.length > 0) return "success";
  return "neutral";
}

function coverageTone(episode: Episode): Tone {
  if (!episode.coverage_mode) return "neutral";
  return episode.coverage_mode === "rich" ? "success" : "warning";
}

export function WorkspaceNav({
  episode,
  active,
  onChange,
}: {
  episode: Episode;
  active: Section;
  onChange: (section: Section) => void;
}) {
  const tones: Partial<Record<Section, Tone>> = {
    sources: sourcesTone(episode),
    dossier: coverageTone(episode),
  };

  const busy = (episode.progress?.pending ?? 0) > 0;

  return (
    <nav className="sm:sticky sm:top-24 sm:w-48 sm:shrink-0">
      <ul className="flex gap-1 overflow-x-auto sm:flex-col sm:gap-0.5 sm:overflow-visible">
        {SECTIONS.map((section) => {
          const { label, icon: Icon } = SECTION_META[section];
          const isActive = section === active;
          const tone = tones[section];
          return (
            <li key={section}>
              <button
                type="button"
                aria-current={isActive ? "page" : undefined}
                onClick={() => onChange(section)}
                className={`flex w-full shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-3 py-2.5 text-ui font-medium transition ${
                  isActive
                    ? "bg-surface-muted text-ink"
                    : "text-ink-muted hover:bg-surface-muted hover:text-ink"
                } ${FOCUS_RING} focus-visible:ring-inset`}
              >
                <Icon size={14} />
                <span className="flex-1 text-left">{label}</span>
                {tone ? <StatusDot tone={tone} /> : null}
              </button>
            </li>
          );
        })}
      </ul>

      {busy ? (
        <div className="mt-3 rounded-md border border-line bg-surface-muted px-3 py-2 sm:mt-4">
          <p className="text-caption text-ink-muted">
            {episode.progress?.stage ?? "Working"}…
          </p>
          {episode.progress?.sources_total ? (
            <div className="mt-1.5">
              <ProgressBar
                finished={episode.progress.sources_analysed}
                total={episode.progress.sources_total}
              />
            </div>
          ) : null}
        </div>
      ) : null}
    </nav>
  );
}
