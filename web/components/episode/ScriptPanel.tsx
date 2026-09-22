"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";

import { ApiError, api } from "@/lib/api";
import { Button, Card, FOCUS_RING, Spinner, Textarea } from "@/components/ui";
import { SegmentCard } from "./SegmentCard";

/* ------------------------------------------------------------------ */
/* script — a timed run-of-show                                       */
/* ------------------------------------------------------------------ */

const STYLES = ["formal", "conversational", "contrarian", "educational"];
const DURATIONS = [30, 45, 60, 90, 120];

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
      type="button"
      onClick={onClick}
      className={`rounded-full border px-3 py-1.5 text-caption transition ${
        active
          ? "border-accent bg-accent text-accent-contrast"
          : "border-line text-ink-muted hover:bg-surface-muted"
      } ${FOCUS_RING}`}
    >
      {children}
    </button>
  );
}

export function ScriptPanel({
  episodeId,
  guestSubmissions,
}: {
  episodeId: string;
  guestSubmissions: number;
}) {
  const queryClient = useQueryClient();
  const [style, setStyle] = useState("conversational");
  const [duration, setDuration] = useState(60);
  const [optimizeOrder, setOptimizeOrder] = useState(true);
  const [includeBonus, setIncludeBonus] = useState(true);
  const [useGuestPrep, setUseGuestPrep] = useState(false);
  const [voiceSample, setVoiceSample] = useState("");
  const [feedback, setFeedback] = useState("");
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
        feedback: feedback.trim() || undefined,
        use_guest_prep: useGuestPrep,
      }),
    onSuccess: () => {
      setFeedback("");
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

  // The POST returns 202 and the writing happens in a job, so isPending covers
  // only the request. A script row exists before its segments do -- that gap is
  // the work. Without this the sole signal was the progress card at the top of
  // the page, so a host who did not scroll up could not tell it had started.
  const generating =
    generate.isPending || (!!script.data && segments.length === 0);

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="text-heading font-semibold text-ink">Script</h2>
        {segments.length ? (
          <Button variant="ghost" onClick={download}>
            <Download size={13} />
            Export PDF
          </Button>
        ) : null}
      </div>

      <div className="mt-3 space-y-4">
        <div>
          <p className="mb-1.5 text-caption font-medium text-ink-muted">
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
          <p className="mb-1.5 text-caption font-medium text-ink-muted">Style</p>
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
          <label className="flex items-center gap-2 text-ui text-ink-muted">
            <input
              type="checkbox"
              checked={optimizeOrder}
              onChange={(e) => setOptimizeOrder(e.target.checked)}
            />
            Let Scripto order the topics for the best flow
          </label>
          <label className="flex items-center gap-2 text-ui text-ink-muted">
            <input
              type="checkbox"
              checked={includeBonus}
              onChange={(e) => setIncludeBonus(e.target.checked)}
            />
            Include backup topics for when a block runs short
          </label>
          {/* Only offered once the guest has actually sent something. */}
          {guestSubmissions > 0 ? (
            <label className="flex items-center gap-2 text-ui text-ink-muted">
              <input
                type="checkbox"
                checked={useGuestPrep}
                onChange={(e) => setUseGuestPrep(e.target.checked)}
              />
              Use what the guest sent ({guestSubmissions}
              {guestSubmissions === 1 ? " submission" : " submissions"}) to shape
              emphasis and order
            </label>
          ) : null}
        </div>

        <Textarea
          rows={2}
          placeholder="Optional: paste a past episode transcript to match your voice"
          value={voiceSample}
          onChange={(e) => setVoiceSample(e.target.value)}
        />

        {segments.length ? (
          <div>
            <p className="mb-1.5 text-caption font-medium text-ink-muted">
              What should change?{" "}
              <span className="font-normal text-ink-subtle">(optional)</span>
            </p>
            <Textarea
              rows={2}
              placeholder="e.g. push harder on the supplement claims, keep questions shorter, spend more time on focus"
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
            />
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={() => {
              setError(null);
              generate.mutate();
            }}
            disabled={generating}
          >
            {generating
              ? "Writing…"
              : feedback.trim()
                ? "Regenerate with these changes"
                : segments.length
                  ? "Regenerate script"
                  : "Generate script"}
          </Button>
          {segments.length && !generating ? (
            <span className="text-caption text-ink-subtle">
              {feedback.trim()
                ? "Your note goes to the writer with the current version, so it revises rather than starting over."
                : hasEdits
                  ? "Your edited blocks will be kept."
                  : "Without a note you get a fresh take. The current version stays saved."}
            </span>
          ) : null}
        </div>
        {generating ? (
          <p className="flex items-center gap-2 rounded border border-line bg-surface-muted px-3 py-2 text-caption text-ink-muted">
            <Spinner />
            Writing your run-of-show. This usually takes a minute or two — it
            appears here when it is ready, and you can keep working meanwhile.
          </p>
        ) : null}
        {error ? <p className="text-body text-status-danger">{error}</p> : null}
      </div>

      {timeline.length ? (
        <div className="mt-6">
          <p className="text-caption font-semibold uppercase tracking-wide text-ink-subtle">
            {script.data?.duration_minutes
              ? `${script.data.duration_minutes}-minute run of show`
              : "Run of show"}{" "}
            · {script.data?.style_preset}
          </p>
          {script.data?.feedback ? (
            <p className="mt-1 text-caption text-ink-subtle">
              Revised from the previous version. You asked: “
              {script.data.feedback}”
            </p>
          ) : null}
          {script.data?.guest_prep_used ? (
            <p className="mt-1 text-caption text-status-success">
              Shaped by what the guest sent through the prep link.
            </p>
          ) : null}
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
          <p className="text-caption font-semibold uppercase tracking-wide text-ink-subtle">
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
