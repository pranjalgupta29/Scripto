"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { useState } from "react";

import { ApiError, api } from "@/lib/api";
import { Badge, Button, Card, EmptyState, Input, StatusPill, Textarea } from "@/components/ui";
import { PrepLinkRow } from "./PrepLinkRow";

/* ------------------------------------------------------------------ */
/* sources                                                             */
/* ------------------------------------------------------------------ */

export function SourcesPanel({
  episodeId,
  sources,
}: {
  episodeId: string;
  sources: { id: string; title: string | null; url: string | null; status: string; error: string | null; added_by: string; type: string; subject?: string; topic?: string | null; identity?: string | null }[];
}) {
  const queryClient = useQueryClient();
  const [url, setUrl] = useState("");
  const [pasted, setPasted] = useState("");
  const [error, setError] = useState<string | null>(null);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["episode", episodeId] });
    queryClient.invalidateQueries({ queryKey: ["dossier", episodeId] });
  };

  const addUrl = useMutation({
    mutationFn: () => api.addSource(episodeId, { url }),
    onSuccess: () => {
      setUrl("");
      invalidate();
    },
    onError: (e: ApiError) => setError(e.message),
  });

  const addText = useMutation({
    mutationFn: () =>
      api.addSource(episodeId, { text: pasted, title: "Pasted note" }),
    onSuccess: () => {
      setPasted("");
      invalidate();
    },
    onError: (e: ApiError) => setError(e.message),
  });

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadSource(episodeId, file),
    onSuccess: invalidate,
    onError: (e: ApiError) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: (sourceId: string) => api.removeSource(episodeId, sourceId),
    onSuccess: invalidate,
  });

  return (
    <Card>
      <h2 className="text-heading font-semibold text-ink">
        Sources ({sources.length})
      </h2>

      <div className="mt-3 space-y-1">
        {sources.map((source) => (
          <div
            key={source.id}
            className="flex items-center justify-between gap-3 rounded border border-line px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-body text-ink">
                {source.url ? (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer"
                    className="underline decoration-ink-subtle underline-offset-2 hover:text-ink"
                  >
                    {source.title ?? source.url}
                  </a>
                ) : (
                  (source.title ?? "Pasted text")
                )}
              </p>
              {source.error ? (
                <p className="truncate text-caption text-status-danger">{source.error}</p>
              ) : null}
            </div>
            {source.subject === "topic" ? (
              <Badge
                tone="info"
                className="max-w-[16rem] truncate"
                title={
                  source.topic
                    ? `Found for the topic "${source.topic}", not about the guest`
                    : "Found for the topic brief, not about the guest"
                }
              >
                {source.topic ? `topic: ${source.topic}` : "topic research"}
              </Badge>
            ) : null}
            {source.identity === "mismatch" ? (
              <Badge tone="danger" title="Someone else with the same name. Nothing was taken from this page.">
                not this person
              </Badge>
            ) : null}
            {source.added_by === "guest" ? (
              <Badge tone="success" title="Sent by the guest through the prep link">
                from guest
              </Badge>
            ) : null}
            <StatusPill status={source.status} />
            <Button variant="danger" onClick={() => remove.mutate(source.id)}>
              Remove
            </Button>
          </div>
        ))}
        {!sources.length ? (
          <EmptyState title="No sources yet." hint="Add one below to start building the dossier." />
        ) : null}
      </div>

      {error ? <p className="mt-3 text-body text-status-danger">{error}</p> : null}

      <div className="mt-4 space-y-3 rounded-md border border-line bg-surface-muted p-4">
        <p className="text-caption font-medium uppercase tracking-wide text-ink-subtle">
          Add a source
        </p>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            addUrl.mutate();
          }}
          className="flex items-center gap-2"
        >
          <Input
            placeholder="Add a URL"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <Button type="submit" variant="ghost" disabled={!url || addUrl.isPending}>
            Add
          </Button>
        </form>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            addText.mutate();
          }}
          className="flex items-start gap-2"
        >
          <Textarea
            rows={2}
            placeholder="Or paste a bio, CV, past talk or internal note"
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
          />
          <Button
            type="submit"
            variant="ghost"
            disabled={!pasted || addText.isPending}
          >
            Add text
          </Button>
        </form>

        <label className="flex cursor-pointer items-center justify-center gap-2 rounded border border-dashed border-line px-3 py-4 text-center text-body text-ink-muted transition hover:border-line-strong hover:bg-surface">
          <input
            type="file"
            accept=".pdf,.docx,.txt,.md"
            className="hidden"
            disabled={upload.isPending}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = ""; // so the same file can be chosen again
              if (!file) return;
              setError(null);
              upload.mutate(file);
            }}
          />
          <Upload size={15} className="shrink-0" />
          {upload.isPending
            ? "Uploading…"
            : "Upload a resume, bio, transcript or notes — PDF, Word, .txt or .md"}
        </label>

        <p className="text-caption text-ink-subtle">
          LinkedIn profile pages cannot be read: LinkedIn blocks it. Open the
          profile, choose More → Save to PDF, and upload that file instead.
          LinkedIn articles and posts work as URLs.
        </p>
      </div>

      <PrepLinkRow episodeId={episodeId} />
    </Card>
  );
}
