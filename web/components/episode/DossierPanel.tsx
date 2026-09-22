"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { SECTION_TITLES } from "@/lib/types";
import { Card, CitationList, Skeleton } from "@/components/ui";

/* ------------------------------------------------------------------ */
/* dossier                                                             */
/* ------------------------------------------------------------------ */

export function DossierPanel({
  episodeId,
  busy,
}: {
  episodeId: string;
  busy: boolean;
}) {
  const dossier = useQuery({
    queryKey: ["dossier", episodeId],
    queryFn: () => api.getDossier(episodeId),
    // Keep refreshing while research runs, so a topic brief or rebuild that
    // lands after the first dossier shows up without a reload.
    refetchInterval: (query) =>
      busy || !query.state.data?.sections.length ? 3000 : false,
  });

  if (!dossier.data?.sections.length) {
    return (
      <Card>
        <h2 className="text-heading font-semibold text-ink">Dossier</h2>
        <p className="mt-2 text-body text-ink-subtle">
          Building — this appears as sources finish parsing.
        </p>
        <div className="mt-4 space-y-2">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-5/6" />
        </div>
      </Card>
    );
  }

  // Topics researched without finding anything usable, shown so the brief
  // never quietly skips one.
  const gaps = dossier.data.coverage_detail?.topic_gaps ?? [];
  const hasBrief = dossier.data.sections.some((s) => s.section === "topic_brief");

  return (
    <Card>
      <h2 className="text-heading font-semibold text-ink">Dossier</h2>
      <div className="mt-4 space-y-6">
        {dossier.data.sections.map((section) => (
          <section key={section.section}>
            <h3 className="text-caption font-semibold uppercase tracking-wide text-ink-subtle">
              {SECTION_TITLES[section.section] ?? section.section}
            </h3>
            <ul className="mt-2 space-y-3">
              {section.items.map((item) => (
                <li key={item.id} className="border-l-2 border-line pl-3">
                  <p className="text-body text-ink">{item.text}</p>
                  <CitationList citations={item.citations} />
                </li>
              ))}
            </ul>
            {section.section === "topic_brief" && gaps.length ? (
              <TopicGaps gaps={gaps} />
            ) : null}
          </section>
        ))}
        {!hasBrief && gaps.length ? (
          <section>
            <h3 className="text-caption font-semibold uppercase tracking-wide text-ink-subtle">
              {SECTION_TITLES.topic_brief}
            </h3>
            <TopicGaps gaps={gaps} />
          </section>
        ) : null}
      </div>
    </Card>
  );
}

function TopicGaps({ gaps }: { gaps: string[] }) {
  return (
    <p className="mt-3 rounded border border-status-warning-border bg-status-warning-bg px-3 py-2 text-ui text-status-warning">
      No sourced material found for: {gaps.join("; ")}. Rewording{" "}
      {gaps.length === 1 ? "the topic" : "a topic"} and saving researches it
      again.
    </p>
  );
}
