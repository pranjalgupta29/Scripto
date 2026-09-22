"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import type { Segment } from "@/lib/types";
import { Badge, Button, CitationList, FOCUS_RING, Textarea } from "@/components/ui";

const TYPE_LABELS: Record<string, string> = {
  opening: "Opening",
  topic: "Topic",
  closing: "Closing",
  bonus: "Backup",
};

export function clock(minute: number) {
  return `${Math.floor(minute / 60)}:${String(minute % 60).padStart(2, "0")}`;
}

export function SegmentCard({
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
      className={`rounded-md border border-line p-4 ${bookend ? "bg-surface-muted" : ""}`}
    >
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-body font-semibold text-ink">
          {segment.title ?? TYPE_LABELS[segment.segment_type]}
        </p>
        <span className="shrink-0 text-caption tabular-nums text-ink-subtle">
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
          className={`mt-2 w-full rounded text-left ${FOCUS_RING}`}
          title="Click to edit"
        >
          {segment.transition_in ? (
            <p className="text-body text-ink-muted">
              <span className="mr-1 text-caption font-medium uppercase tracking-wide text-ink-subtle">
                {segment.segment_type === "opening" ? "Hook" : "Transition"}
              </span>
              <span className="italic">“{segment.transition_in}”</span>
            </p>
          ) : null}
          {segment.host_script ? (
            <p className="mt-1 text-body text-ink-muted">
              <span className="mr-1 text-caption font-medium uppercase tracking-wide text-ink-subtle">
                Say
              </span>
              {segment.host_script}
            </p>
          ) : null}
          <p className="mt-2 font-medium text-ink">{segment.question}</p>
        </button>
      )}

      {segment.deeper_questions?.length ? (
        <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-body text-ink-muted">
          {segment.deeper_questions.map((q, i) => (
            <li key={i}>{q}</li>
          ))}
        </ol>
      ) : null}

      {segment.rationale ? (
        <p className="mt-2 text-caption text-ink-subtle">
          <span className="font-medium">Why: </span>
          {segment.rationale}
        </p>
      ) : null}

      {segment.expected_direction ? (
        <p className="mt-1 text-caption text-ink-subtle">
          <span className="font-medium">Likely direction: </span>
          {segment.expected_direction}
        </p>
      ) : null}

      {segment.followups?.length ? (
        <ul className="mt-2 list-disc pl-5 text-caption text-ink-muted">
          {segment.followups.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      ) : null}

      {segment.risk_flags?.length || segment.flagged_unsourced ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {segment.flagged_unsourced ? (
            <Badge tone="danger">
              Not backed by the research — check before asking
            </Badge>
          ) : null}
          {segment.risk_flags.map((flag, i) => (
            <Badge key={i} tone="warning">
              {flag}
            </Badge>
          ))}
        </div>
      ) : null}

      <CitationList citations={segment.citations} />

      {segment.edited_by_user ? (
        <p className="mt-2 text-caption text-ink-subtle">edited by you</p>
      ) : null}
    </div>
  );
}
