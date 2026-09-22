"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { Candidate } from "@/lib/types";
import { Button, Card } from "@/components/ui";

/* ------------------------------------------------------------------ */
/* identity — never auto-selected                                     */
/* ------------------------------------------------------------------ */

export function IdentityStep({
  episodeId,
  candidates,
}: {
  episodeId: string;
  candidates: Candidate[];
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const identify = useMutation({
    mutationFn: () => api.identify(episodeId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["episode", episodeId] }),
    onError: (e: ApiError) => setError(e.message),
  });

  const confirm = useMutation({
    mutationFn: (index: number) => api.confirmGuest(episodeId, index),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["episode", episodeId] }),
    onError: (e: ApiError) => setError(e.message),
  });

  // Kick off identification once when the page first opens.
  useEffect(() => {
    if (!candidates.length && !identify.isPending && !identify.isSuccess) {
      identify.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Card>
      <h2 className="text-heading font-semibold text-ink">
        Which one is your guest?
      </h2>
      <p className="mt-1 text-ui text-ink-muted">
        Picking the wrong person poisons everything downstream, so this step
        can&apos;t be skipped.
      </p>

      {error ? <p className="mt-3 text-body text-status-danger">{error}</p> : null}

      {identify.isPending ? (
        <p className="mt-4 text-body text-ink-subtle">Searching…</p>
      ) : null}

      <div className="mt-4 space-y-2">
        {candidates.map((candidate, index) => (
          <div
            key={index}
            className="flex items-start justify-between gap-4 rounded-md border border-line p-4"
          >
            <div className="min-w-0">
              <p className="font-medium text-ink">
                {candidate.name}
                <span className="ml-2 text-caption font-normal text-ink-subtle">
                  {Math.round(candidate.confidence * 100)}% match
                </span>
              </p>
              <p className="text-body text-ink-muted">
                {candidate.headline}
                {candidate.employer ? ` · ${candidate.employer}` : ""}
              </p>
              {candidate.reasoning ? (
                <p className="mt-1 text-caption text-ink-subtle">
                  {candidate.reasoning}
                </p>
              ) : null}
              {candidate.evidence_urls?.length ? (
                <div className="mt-1 flex flex-wrap gap-2">
                  {candidate.evidence_urls.slice(0, 3).map((url) => (
                    <a
                      key={url}
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-caption text-ink-subtle underline underline-offset-2 hover:text-ink"
                    >
                      evidence
                    </a>
                  ))}
                </div>
              ) : null}
            </div>
            <Button
              variant="accent"
              onClick={() => confirm.mutate(index)}
              disabled={confirm.isPending}
            >
              This one
            </Button>
          </div>
        ))}
      </div>

      {!identify.isPending && !candidates.length ? (
        <div className="mt-4">
          <p className="text-body text-ink-muted">
            No candidates yet. Try a more specific disambiguator.
          </p>
          <Button
            variant="ghost"
            className="mt-2"
            onClick={() => identify.mutate()}
          >
            Search again
          </Button>
        </div>
      ) : null}
    </Card>
  );
}
