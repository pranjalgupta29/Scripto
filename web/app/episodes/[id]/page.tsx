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
/* 4. topics — the host's own, plus suggestions that say what backs them */
/* ------------------------------------------------------------------ */

const MAX_TOPICS = 6;

function TopicsPanel({ episodeId }: { episodeId: string }) {
  const queryClient = useQueryClient();
  const [topics, setTopics] = useState<string[]>(["", "", ""]);
  const [hydrated, setHydrated] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const stored = useQuery({
    queryKey: ["topics", episodeId],
    queryFn: () => api.getTopics(episodeId),
  });

  // Show topics saved on an earlier visit instead of starting blank.
  useEffect(() => {
    if (hydrated || !stored.data) return;
    if (stored.data.length) {
      const texts = stored.data.map((t) => t.text);
      while (texts.length < 3) texts.push("");
      setTopics(texts);
      setSaved(true);
    }
    setHydrated(true);
  }, [stored.data, hydrated]);

  const suggest = useMutation({
    mutationFn: () => api.suggestTopics(episodeId),
    onError: (e: ApiError) => setError(e.message),
  });

  const save = useMutation({
    mutationFn: () => api.setTopics(episodeId, topics.filter((t) => t.trim())),
    onSuccess: () => {
      setSaved(true);
      queryClient.invalidateQueries({ queryKey: ["topics", episodeId] });
      queryClient.invalidateQueries({ queryKey: ["episode", episodeId] });
    },
    onError: (e: ApiError) => setError(e.message),
  });

  const filled = topics.filter((t) => t.trim()).length;
  const alreadyHas = (text: string) =>
    topics.some((t) => t.trim().toLowerCase() === text.trim().toLowerCase());

  const addTopic = (text: string) => {
    if (alreadyHas(text)) return;
    const empty = topics.findIndex((t) => !t.trim());
    if (empty >= 0) {
      const next = [...topics];
      next[empty] = text;
      setTopics(next);
    } else if (topics.length < MAX_TOPICS) {
      setTopics([...topics, text]);
    }
    setSaved(false);
  };

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold">Topics</h2>
          <p className="mt-1 text-xs text-black/55">
            3 to 6 topics you want the episode to cover.
          </p>
        </div>
        <Button
          variant="ghost"
          onClick={() => {
            setError(null);
            suggest.mutate();
          }}
          disabled={suggest.isPending}
        >
          {suggest.isPending ? "Thinking…" : "Suggest topics"}
        </Button>
      </div>

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
                onClick={() => {
                  setTopics(topics.filter((_, j) => j !== i));
                  setSaved(false);
                }}
              >
                ×
              </Button>
            ) : null}
          </div>
        ))}
      </div>

      <div className="mt-3 flex items-center gap-2">
        {topics.length < MAX_TOPICS ? (
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

      {suggest.data ? (
        <div className="mt-5 border-t border-black/10 pt-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-black/45">
            Suggested from the episode title and the research
          </p>
          {!suggest.data.suggestions.length ? (
            <p className="mt-2 text-sm text-black/50">No new suggestions.</p>
          ) : null}
          <ul className="mt-2 space-y-2">
            {suggest.data.suggestions.map((s) => {
              const added = alreadyHas(s.text);
              return (
                <li
                  key={s.text}
                  className="flex items-start justify-between gap-3 rounded-md border border-black/10 p-3"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{s.text}</p>
                    {s.why ? (
                      <p className="mt-0.5 text-xs text-black/55">{s.why}</p>
                    ) : null}
                    <span
                      className={`mt-1 inline-block rounded border px-1.5 py-0.5 text-[11px] ${
                        s.basis === "research"
                          ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                          : "border-amber-300 bg-amber-50 text-amber-900"
                      }`}
                    >
                      {s.basis === "research"
                        ? "Backed by research"
                        : "From the title only — not researched"}
                    </span>
                    <CitationList citations={s.citations.slice(0, 1)} />
                  </div>
                  <Button
                    variant="ghost"
                    disabled={added || filled >= MAX_TOPICS}
                    onClick={() => addTopic(s.text)}
                  >
                    {added ? "Added" : "Add"}
                  </Button>
                </li>
              );
            })}
          </ul>
          {filled >= MAX_TOPICS ? (
            <p className="mt-2 text-xs text-black/50">
              Six topics is the maximum. Remove one to add another.
            </p>
          ) : (
            <p className="mt-2 text-xs text-black/50">
              Added suggestions still need saving.
            </p>
          )}
        </div>
      ) : null}
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* 5. script — a timed run-of-show                                     */
/* ------------------------------------------------------------------ */

const STYLES = ["formal", "conversational", "contrarian", "educational"];
const DURATIONS = [30, 45, 60, 90, 120];

function clock(minute: number) {
  return `${Math.floor(minute / 60)}:${String(minute % 60).padStart(2, "0")}`;
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-xs capitalize transition ${
        active
          ? "border-ink bg-ink text-white"
          : "border-black/15 hover:border-black/40"
      }`}
    >
      {children}
    </button>
  );
}

function ScriptPanel({ episodeId }: { episodeId: string }) {
  const queryClient = useQueryClient();
  const [style, setStyle] = useState("conversational");
  const [duration, setDuration] = useState(60);
  const [optimizeOrder, setOptimizeOrder] = useState(true);
  const [includeBonus, setIncludeBonus] = useState(true);
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
        duration_minutes: duration,
        optimize_order: optimizeOrder,
        include_bonus: includeBonus,
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

  const segments = script.data?.segments ?? [];
  const timeline = segments.filter((s) => s.segment_type !== "bonus");
  const backups = segments.filter((s) => s.segment_type === "bonus");
  const hasEdits = segments.some((s) => s.edited_by_user);

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Script</h2>
        {segments.length ? (
          <Button variant="ghost" onClick={download}>
            Export PDF
          </Button>
        ) : null}
      </div>

      <div className="mt-3 space-y-4">
        <div>
          <p className="mb-1.5 text-xs font-medium text-black/60">
            Interview length
          </p>
          <div className="flex flex-wrap gap-2">
            {DURATIONS.map((minutes) => (
              <Chip
                key={minutes}
                active={duration === minutes}
                onClick={() => setDuration(minutes)}
              >
                {minutes >= 60 && minutes % 60 === 0
                  ? `${minutes / 60} hr`
                  : `${minutes} min`}
              </Chip>
            ))}
          </div>
        </div>

        <div>
          <p className="mb-1.5 text-xs font-medium text-black/60">Style</p>
          <div className="flex flex-wrap gap-2">
            {STYLES.map((preset) => (
              <Chip
                key={preset}
                active={style === preset}
                onClick={() => setStyle(preset)}
              >
                {preset}
              </Chip>
            ))}
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="flex items-center gap-2 text-xs text-black/70">
            <input
              type="checkbox"
              checked={optimizeOrder}
              onChange={(e) => setOptimizeOrder(e.target.checked)}
            />
            Let Scripto order the topics for the best flow
          </label>
          <label className="flex items-center gap-2 text-xs text-black/70">
            <input
              type="checkbox"
              checked={includeBonus}
              onChange={(e) => setIncludeBonus(e.target.checked)}
            />
            Include backup topics for when a block runs short
          </label>
        </div>

        <Textarea
          rows={2}
          placeholder="Optional: paste a past episode transcript to match your voice"
          value={voiceSample}
          onChange={(e) => setVoiceSample(e.target.value)}
        />

        <div className="flex items-center gap-3">
          <Button
            onClick={() => {
              setError(null);
              generate.mutate();
            }}
            disabled={generate.isPending}
          >
            {generate.isPending
              ? "Generating…"
              : segments.length
                ? "Regenerate script"
                : "Generate script"}
          </Button>
          {hasEdits ? (
            <span className="text-xs text-amber-800">
              Regenerating replaces your edits.
            </span>
          ) : null}
        </div>
        {error ? <p className="text-sm text-red-700">{error}</p> : null}
      </div>

      {timeline.length ? (
        <div className="mt-6">
          <p className="text-xs font-semibold uppercase tracking-wide text-black/45">
            {script.data?.duration_minutes
              ? `${script.data.duration_minutes}-minute run of show`
              : "Run of show"}{" "}
            · {script.data?.style_preset}
          </p>
          <div className="mt-3 space-y-3">
            {timeline.map((segment) => (
              <SegmentCard
                key={segment.id}
                scriptId={script.data!.id}
                segment={segment}
                episodeId={episodeId}
              />
            ))}
          </div>
        </div>
      ) : null}

      {backups.length ? (
        <div className="mt-6">
          <p className="text-xs font-semibold uppercase tracking-wide text-black/45">
            Backup topics — if a block runs short or falls flat
          </p>
          <div className="mt-3 space-y-3">
            {backups.map((segment) => (
              <SegmentCard
                key={segment.id}
                scriptId={script.data!.id}
                segment={segment}
                episodeId={episodeId}
              />
            ))}
          </div>
        </div>
      ) : null}
    </Card>
  );
}

const TYPE_LABELS: Record<string, string> = {
  opening: "Opening",
  topic: "Topic",
  closing: "Closing",
  bonus: "Backup",
};

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
  const [transition, setTransition] = useState(segment.transition_in ?? "");

  const save = useMutation({
    mutationFn: () =>
      api.patchSegment(scriptId, segment.id, {
        question,
        transition_in: transition,
      }),
    onSuccess: () => {
      setEditing(false);
      queryClient.invalidateQueries({ queryKey: ["script", episodeId] });
    },
  });

  const timing =
    segment.start_minute !== null && segment.planned_minutes
      ? `${clock(segment.start_minute)}–${clock(
          segment.start_minute + segment.planned_minutes,
        )} · ${segment.planned_minutes} min`
      : TYPE_LABELS[segment.segment_type];
  const bookend =
    segment.segment_type === "opening" || segment.segment_type === "closing";

  return (
    <div
      className={`rounded-md border border-black/10 p-4 ${bookend ? "bg-black/[0.02]" : ""}`}
    >
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm font-semibold">
          {segment.title ?? TYPE_LABELS[segment.segment_type]}
        </p>
        <span className="shrink-0 text-xs tabular-nums text-black/45">
          {timing}
        </span>
      </div>

      {editing ? (
        <div className="mt-3 space-y-2">
          {segment.segment_type !== "bonus" ? (
            <Textarea
              rows={2}
              placeholder={
                segment.segment_type === "opening"
                  ? "Hook"
                  : "Transition into this block"
              }
              value={transition}
              onChange={(e) => setTransition(e.target.value)}
            />
          ) : null}
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
                setTransition(segment.transition_in ?? "");
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
          className="mt-2 w-full text-left"
          title="Click to edit"
        >
          {segment.transition_in ? (
            <p className="text-sm text-black/60">
              <span className="mr-1 text-[11px] font-medium uppercase tracking-wide text-black/40">
                {segment.segment_type === "opening" ? "Hook" : "Transition"}
              </span>
              <span className="italic">“{segment.transition_in}”</span>
            </p>
          ) : null}
          {segment.host_script ? (
            <p className="mt-1 text-sm text-black/70">
              <span className="mr-1 text-[11px] font-medium uppercase tracking-wide text-black/40">
                Say
              </span>
              {segment.host_script}
            </p>
          ) : null}
          <p className="mt-2 font-medium">{segment.question}</p>
        </button>
      )}

      {segment.deeper_questions?.length ? (
        <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-sm text-black/75">
          {segment.deeper_questions.map((q, i) => (
            <li key={i}>{q}</li>
          ))}
        </ol>
      ) : null}

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

      {segment.risk_flags?.length || segment.flagged_unsourced ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {segment.flagged_unsourced ? (
            <span className="rounded border border-red-200 bg-red-50 px-1.5 py-0.5 text-[11px] text-red-800">
              Not backed by the research — check before asking
            </span>
          ) : null}
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
