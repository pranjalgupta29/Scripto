"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { ApiError, api } from "@/lib/api";
import { Badge, CoverageBanner, ProgressBar } from "@/components/ui";
import { IdentityStep } from "@/components/episode/IdentityStep";
import { SourcesPanel } from "@/components/episode/SourcesPanel";
import { DossierPanel } from "@/components/episode/DossierPanel";
import { TopicsPanel } from "@/components/episode/TopicsPanel";
import { ScriptPanel } from "@/components/episode/ScriptPanel";
import { WorkspaceNav, type Section } from "@/components/episode/WorkspaceNav";

const SECTIONS: Section[] = ["sources", "dossier", "topics", "script"];

export default function EpisodePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const episode = useQuery({
    queryKey: ["episode", id],
    queryFn: () => api.getEpisode(id),
    // Spec: client polls job status every 2s. No websockets.
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 2000;
      const busy = (data.progress?.pending ?? 0) > 0;
      return busy || data.status === "ingesting" ? 2000 : false;
    },
  });

  const data = episode.data;
  const needsIdentity = data && !data.guest;

  const paramSection = searchParams.get("section");
  const [section, setSection] = useState<Section>(
    (paramSection && SECTIONS.includes(paramSection as Section)
      ? (paramSection as Section)
      : "sources"),
  );

  const goTo = (next: Section) => {
    setSection(next);
    router.replace(`${pathname}?section=${next}`, { scroll: false });
  };

  if (episode.isLoading) return <p className="text-body text-ink-subtle">Loading…</p>;
  if (episode.error)
    return (
      <p className="text-body text-status-danger">
        {(episode.error as ApiError).message}
      </p>
    );
  if (!data) return null;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/" className="text-caption text-ink-subtle hover:text-ink">
          ← All episodes
        </Link>
        <h1 className="mt-2 font-serif text-display font-semibold text-ink">
          {data.title}
        </h1>
        <p className="mt-1 flex flex-wrap items-center gap-2 text-body text-ink-muted">
          {data.guest?.name ?? data.guest_name}
          {data.guest?.headline ? ` · ${data.guest.headline}` : ""}
          <Badge tone="neutral">{data.status}</Badge>
        </p>
      </div>

      {needsIdentity ? (
        <>
          {data.progress && data.progress.pending > 0 ? (
            <div className="rounded-lg border border-line bg-surface p-4">
              <p className="mb-2 text-body font-medium text-ink">
                {data.progress.stage ?? "Working"}…
              </p>
              {data.progress.sources_total ? (
                <ProgressBar
                  finished={data.progress.sources_analysed}
                  total={data.progress.sources_total}
                />
              ) : null}
            </div>
          ) : null}
          <IdentityStep episodeId={id} candidates={data.candidates} />
        </>
      ) : (
        <>
          <CoverageBanner mode={data.coverage_mode} detail={data.coverage_detail} />

          <div className="flex flex-col gap-6 sm:flex-row sm:items-start">
            <WorkspaceNav episode={data} active={section} onChange={goTo} />

            {/* Every panel stays mounted -- only visibility toggles. The
                original page rendered all of them at once (just stacked), so
                switching sections must not unmount one and drop its
                in-progress local state (an unsaved topic draft, a half-typed
                source URL) or interrupt a query's refetchInterval polling. */}
            <div className="min-w-0 flex-1">
              <div className={section === "sources" ? "" : "hidden"}>
                <SourcesPanel episodeId={id} sources={data.sources} />
              </div>
              <div className={section === "dossier" ? "" : "hidden"}>
                <DossierPanel
                  episodeId={id}
                  busy={(data.progress?.pending ?? 0) > 0}
                />
              </div>
              <div className={section === "topics" ? "" : "hidden"}>
                <TopicsPanel episodeId={id} />
              </div>
              <div className={section === "script" ? "" : "hidden"}>
                <ScriptPanel
                  episodeId={id}
                  guestSubmissions={
                    data.sources.filter((s) => s.added_by === "guest").length
                  }
                />
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
