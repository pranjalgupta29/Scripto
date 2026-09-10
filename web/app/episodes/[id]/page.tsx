"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { SECTION_TITLES, type Candidate, type Segment } from "@/lib/types";
import {
  Button,
  Card,
  CitationList,
  CoverageBanner,
  Input,
  ProgressBar,
  StatusPill,
  Textarea,
} from "@/components/ui";

export default function EpisodePage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();

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

  if (episode.isLoading) return <p className="text-sm text-black/50">Loading…</p>;
  if (episode.error)
    return (
      <p className="text-sm text-red-700">
        {(episode.error as ApiError).message}
      </p>
    );
  if (!data) return null;

  return (
    <div className="space-y-8">
      <div>
        <Link href="/" className="text-xs text-black/50 hover:text-black">
          ← All episodes
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          {data.title}
        </h1>
        <p className="mt-1 text-sm text-black/60">
          {data.guest?.name ?? data.guest_name}
          {data.guest?.headline ? ` · ${data.guest.headline}` : ""}
          <span className="ml-2 rounded bg-black/5 px-1.5 py-0.5 text-xs">
            {data.status}
          </span>
        </p>
      </div>

      {data.progress && data.progress.pending > 0 ? (
        <Card>
          <p className="mb-2 text-sm font-medium">Ingesting…</p>
          <ProgressBar
            finished={data.progress.finished}
            total={data.progress.total}
          />
          <p className="mt-2 text-xs text-black/50">
            Sources appear below as they land. You can keep working.
          </p>
        </Card>
      ) : null}

      {needsIdentity ? (
        <IdentityStep episodeId={id} candidates={data.candidates} />
      ) : (
        <>
          <CoverageBanner
            mode={data.coverage_mode}
            detail={data.coverage_detail}
          />
          <SourcesPanel episodeId={id} sources={data.sources} />
          <DossierPanel episodeId={id} />
          <TopicsPanel episodeId={id} />
          <ScriptPanel episodeId={id} />
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 1. identity — never auto-selected                                   */
/* ------------------------------------------------------------------ */

function IdentityStep({
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
      <h2 className="text-sm font-semibold">Which one is your guest?</h2>
      <p className="mt-1 text-xs text-black/55">
        Picking the wrong person poisons everything downstream, so this step
        can&apos;t be skipped.
      </p>

      {error ? <p className="mt-3 text-sm text-red-700">{error}</p> : null}

      {identify.isPending ? (
        <p className="mt-4 text-sm text-black/50">Searching…</p>
      ) : null}

      <div className="mt-4 space-y-2">
        {candidates.map((candidate, index) => (
          <div
            key={index}
            className="flex items-start justify-between gap-4 rounded-md border border-black/10 p-4"
          >
            <div className="min-w-0">
              <p className="font-medium">
                {candidate.name}
                <span className="ml-2 text-xs font-normal text-black/45">
                  {Math.round(candidate.confidence * 100)}% match
                </span>
              </p>
              <p className="text-sm text-black/60">
                {candidate.headline}
                {candidate.employer ? ` · ${candidate.employer}` : ""}
              </p>
              {candidate.reasoning ? (
                <p className="mt-1 text-xs text-black/45">
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
                      className="text-xs text-black/45 underline underline-offset-2 hover:text-black"
                    >
                      evidence
                    </a>
                  ))}
                </div>
              ) : null}
            </div>
            <Button
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
          <p className="text-sm text-black/60">
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

/* ------------------------------------------------------------------ */
/* 2. sources                                                          */
/* ------------------------------------------------------------------ */

function SourcesPanel({
  episodeId,
  sources,
}: {
  episodeId: string;
  sources: { id: string; title: string | null; url: string | null; status: string; error: string | null; added_by: string; type: string }[];
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

  const remove = useMutation({
    mutationFn: (sourceId: string) => api.removeSource(episodeId, sourceId),
    onSuccess: invalidate,
  });

  return (
    <Card>
      <h2 className="text-sm font-semibold">Sources ({sources.length})</h2>

      <div className="mt-3 space-y-1">
        {sources.map((source) => (
          <div
            key={source.id}
            className="flex items-center justify-between gap-3 rounded border border-black/5 px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm">
                {source.url ? (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer"
                    className="underline decoration-black/20 underline-offset-2 hover:text-black"
                  >
                    {source.title ?? source.url}
                  </a>
                ) : (
                  (source.title ?? "Pasted text")
                )}
              </p>
              {source.error ? (
                <p className="truncate text-xs text-red-700">{source.error}</p>
              ) : null}
            </div>
            <StatusPill status={source.status} />
            <Button variant="danger" onClick={() => remove.mutate(source.id)}>
              Remove
            </Button>
          </div>
        ))}
        {!sources.length ? (
          <p className="text-sm text-black/50">No sources yet.</p>
        ) : null}
      </div>

      {error ? <p className="mt-3 text-sm text-red-700">{error}</p> : null}

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            addUrl.mutate();
          }}
          className="flex gap-2"
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
          className="space-y-2"
        >
          <Textarea
            rows={3}
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
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* 3. dossier                                                          */
/* ------------------------------------------------------------------ */

function DossierPanel({ episodeId }: { episodeId: string }) {
  const dossier = useQuery({
    queryKey: ["dossier", episodeId],
    queryFn: () => api.getDossier(episodeId),
    refetchInterval: (query) =>
      query.state.data?.sections.length ? false : 3000,
  });

  if (!dossier.data?.sections.length) {
    return (
      <Card>
        <h2 className="text-sm font-semibold">Dossier</h2>
        <p className="mt-2 text-sm text-black/50">
          Building — this appears as sources finish parsing.
        </p>
      </Card>
    );
  }

  return (
    <Card>
      <h2 className="text-sm font-semibold">Dossier</h2>
      <div className="mt-4 space-y-6">
        {dossier.data.sections.map((section) => (
          <section key={section.section}>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-black/45">
              {SECTION_TITLES[section.section] ?? section.section}
            </h3>
            <ul className="mt-2 space-y-3">
              {section.items.map((item) => (
                <li key={item.id} className="border-l-2 border-black/10 pl-3">
                  <p className="text-sm">{item.text}</p>
                  <CitationList citations={item.citations} />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* 4. topics — the user supplies these, we never suggest them           */
/* ------------------------------------------------------------------ */

function TopicsPanel({ episodeId }: { episodeId: string }) {
  const queryClient = useQueryClient();
  const [topics, setTopics] = useState<string[]>(["", "", ""]);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => api.setTopics(episodeId, topics.filter((t) => t.trim())),
    onSuccess: () => {
      setSaved(true);
      queryClient.invalidateQueries({ queryKey: ["episode", episodeId] });
    },
    onError: (e: ApiError) => setError(e.message),
  });

  const filled = topics.filter((t) => t.trim()).length;

  return (
    <Card>
      <h2 className="text-sm font-semibold">Topics</h2>
      <p className="mt-1 text-xs text-black/55">
        3 to 6 topics you want the episode to cover.
      </p>

      <div className="mt-3 space-y-2">
        {topics.map((topic, i) => (
          <div key={i} className="flex gap-2">
            <Input
              placeholder={`Topic ${i + 1}`}
              value={topic}
              onChange={(e) => {
                const next = [...topics];
                next[i] = e.target.value;
                setTopics(next);
                setSaved(false);
              }}
            />
            {topics.length > 3 ? (
              <Button
                variant="ghost"
                onClick={() => setTopics(topics.filter((_, j) => j !== i))}
              >
                ×
              </Button>
            ) : null}
          </div>
        ))}
      </div>

      <div className="mt-3 flex items-center gap-2">
        {topics.length < 6 ? (
          <Button variant="ghost" onClick={() => setTopics([...topics, ""])}>
            Add topic
          </Button>
        ) : null}
        <Button
          onClick={() => {
            setError(null);
            save.mutate();
          }}
          disabled={filled < 3 || save.isPending}
        >
          {save.isPending ? "Saving…" : "Save topics"}
        </Button>
        {saved ? <span className="text-xs text-emerald-700">Saved</span> : null}
      </div>
      {error ? <p className="mt-2 text-sm text-red-700">{error}</p> : null}
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* 5. script                                                           */
/* ------------------------------------------------------------------ */

const STYLES = ["formal", "conversational", "contrarian", "educational"];

function ScriptPanel({ episodeId }: { episodeId: string }) {
  const queryClient = useQueryClient();
  const [style, setStyle] = useState("conversational");
  const [voiceSample, setVoiceSample] = useState("");
  const [error, setError] = useState<string | null>(null);

  const script = useQuery({
    queryKey: ["script", episodeId],
    queryFn: () => api.getScript(episodeId),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.segments.length ? false : 2500,
  });

  const generate = useMutation({
    mutationFn: () =>
      api.createScript(episodeId, {
        style_preset: style,
        voice_sample: voiceSample || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["script", episodeId] });
      queryClient.invalidateQueries({ queryKey: ["episode", episodeId] });
    },
    onError: (e: ApiError) => setError(e.message),
  });

  const download = async () => {
    const blob = await api.downloadExport(episodeId);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "scripto-episode.pdf";
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Script</h2>
        {script.data?.segments.length ? (
          <Button variant="ghost" onClick={download}>
            Export PDF
          </Button>
        ) : null}
      </div>

      <div className="mt-3 space-y-3">
        <div className="flex flex-wrap gap-2">
          {STYLES.map((preset) => (
            <button
              key={preset}
              onClick={() => setStyle(preset)}
              className={`rounded-full border px-3 py-1 text-xs capitalize transition ${
                style === preset
                  ? "border-ink bg-ink text-white"
                  : "border-black/15 hover:border-black/40"
              }`}
            >
              {preset}
            </button>
          ))}
        </div>

        <Textarea
          rows={2}
          placeholder="Optional: paste a past episode transcript to match your voice"
          value={voiceSample}
          onChange={(e) => setVoiceSample(e.target.value)}
        />

        <Button
          onClick={() => {
            setError(null);
            generate.mutate();
          }}
          disabled={generate.isPending}
        >
          {generate.isPending
            ? "Generating…"
            : script.data?.segments.length
              ? "Regenerate script"
              : "Generate script"}
        </Button>
        {error ? <p className="text-sm text-red-700">{error}</p> : null}
      </div>

      {script.data?.segments.length ? (
        <div className="mt-6 space-y-4">
          {script.data.segments.map((segment) => (
            <SegmentCard
              key={segment.id}
              scriptId={script.data!.id}
              segment={segment}
              episodeId={episodeId}
            />
          ))}
        </div>
      ) : null}
    </Card>
  );
}

function SegmentCard({
  scriptId,
  segment,
  episodeId,
}: {
  scriptId: string;
  segment: Segment;
  episodeId: string;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [question, setQuestion] = useState(segment.question);

  const save = useMutation({
    mutationFn: () =>
      api.patchSegment(scriptId, segment.id, { question }),
    onSuccess: () => {
      setEditing(false);
      queryClient.invalidateQueries({ queryKey: ["script", episodeId] });
    },
  });

  return (
    <div className="rounded-md border border-black/10 p-4">
      {editing ? (
        <div className="space-y-2">
          <Textarea
            rows={2}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <div className="flex gap-2">
            <Button onClick={() => save.mutate()} disabled={save.isPending}>
              Save
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setQuestion(segment.question);
                setEditing(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setEditing(true)}
          className="w-full text-left"
          title="Click to edit"
        >
          <p className="font-medium">{segment.question}</p>
        </button>
      )}

      {segment.rationale ? (
        <p className="mt-2 text-xs text-black/55">
          <span className="font-medium">Why: </span>
          {segment.rationale}
        </p>
      ) : null}

      {segment.expected_direction ? (
        <p className="mt-1 text-xs text-black/55">
          <span className="font-medium">Likely direction: </span>
          {segment.expected_direction}
        </p>
      ) : null}

      {segment.followups?.length ? (
        <ul className="mt-2 list-disc pl-5 text-xs text-black/60">
          {segment.followups.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      ) : null}

      {segment.risk_flags?.length ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {segment.risk_flags.map((flag, i) => (
            <span
              key={i}
              className="rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-900"
            >
              {flag}
            </span>
          ))}
        </div>
      ) : null}

      <CitationList citations={segment.citations} />

      {segment.edited_by_user ? (
        <p className="mt-2 text-[11px] text-black/40">edited by you</p>
      ) : null}
    </div>
  );
}
