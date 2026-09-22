"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { PrepQuestion } from "@/lib/types";
import { Badge, Button, FOCUS_RING, Textarea } from "@/components/ui";

const PREP_STYLES: { value: string; label: string; hint: string }[] = [
  {
    value: "conversational",
    label: "Conversational",
    hint: "what they are passionate about and enjoy talking about",
  },
  {
    value: "formal",
    label: "Formal",
    hint: "what they will go into detail on, and where they must be careful",
  },
  {
    value: "contrarian",
    label: "Contrarian",
    hint: "which arguments they enjoy, where they welcome pushback",
  },
  {
    value: "educational",
    label: "Educational",
    hint: "what they like explaining, which misunderstandings frustrate them",
  },
];

/**
 * Ask the guest — the one source no search can reach. The questionnaire is
 * drafted from the research, edited by the host, and only then published.
 */
export function PrepLinkRow({ episodeId }: { episodeId: string }) {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [style, setStyle] = useState("conversational");
  const [questions, setQuestions] = useState<PrepQuestion[] | null>(null);
  const [unsaved, setUnsaved] = useState(false);

  const saved = useQuery({
    queryKey: ["prep-questions", episodeId],
    queryFn: () => api.getPrepQuestions(episodeId),
  });
  const link = useQuery({
    queryKey: ["prep-link", episodeId],
    queryFn: () => api.getPrepLink(episodeId),
  });

  // Show what was approved earlier instead of starting blank.
  useEffect(() => {
    if (questions !== null || !saved.data) return;
    setQuestions(saved.data.questions);
    setStyle(saved.data.style);
  }, [saved.data, questions]);

  const draft = useMutation({
    mutationFn: () => api.suggestPrepQuestions(episodeId, style),
    onSuccess: (data) => {
      setQuestions(data.questions);
      setUnsaved(true);
    },
    onError: (e: ApiError) => setError(e.message),
  });

  const save = useMutation({
    mutationFn: () => api.savePrepQuestions(episodeId, style, questions ?? []),
    onSuccess: (data) => {
      setQuestions(data.questions);
      setUnsaved(false);
      queryClient.invalidateQueries({ queryKey: ["prep-questions", episodeId] });
    },
    onError: (e: ApiError) => setError(e.message),
  });

  const refreshLink = () =>
    queryClient.invalidateQueries({ queryKey: ["prep-link", episodeId] });
  const create = useMutation({
    mutationFn: () => api.createPrepLink(episodeId),
    onSuccess: refreshLink,
    onError: (e: ApiError) => setError(e.message),
  });
  const revoke = useMutation({
    mutationFn: () => api.revokePrepLink(episodeId),
    onSuccess: refreshLink,
  });

  const list = questions ?? [];
  const approved = (saved.data?.questions.length ?? 0) > 0;
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const url = link.data ? `${origin}${link.data.path}` : null;

  const edit = (index: number, text: string) => {
    setQuestions(list.map((q, i) => (i === index ? { ...q, text } : q)));
    setUnsaved(true);
  };

  return (
    <div className="mt-4 border-t border-line pt-4">
      <p className="text-ui font-medium text-ink">Ask the guest</p>
      <p className="mt-0.5 text-caption text-ink-subtle">
        Broad questions about what they want from the conversation — never the
        ones you plan to ask on air, so the interview keeps its surprises.
        Drafted from the research, edited by you, then sent as one link. The
        guest answers and can attach a CV; no account needed.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {PREP_STYLES.map((preset) => (
          <button
            key={preset.value}
            type="button"
            title={preset.hint}
            onClick={() => {
              setStyle(preset.value);
              setUnsaved(true);
            }}
            className={`rounded-full border px-3 py-1.5 text-caption transition ${
              style === preset.value
                ? "border-accent bg-accent text-accent-contrast"
                : "border-line text-ink-muted hover:bg-surface-muted"
            } ${FOCUS_RING}`}
          >
            {preset.label}
          </button>
        ))}
        <Button
          variant="ghost"
          onClick={() => {
            setError(null);
            draft.mutate();
          }}
          disabled={draft.isPending}
        >
          {draft.isPending
            ? "Drafting…"
            : list.length
              ? "Redraft from research"
              : "Draft questions"}
        </Button>
      </div>

      {error ? <p className="mt-3 text-body text-status-danger">{error}</p> : null}

      {list.length ? (
        <div className="mt-3 space-y-2">
          {list.map((question, index) => (
            <div key={index} className="rounded-md border border-line p-3">
              <div className="flex items-center justify-between gap-2">
                {question.basis === "research" ? (
                  <Badge tone="success" title={question.why ?? "Grounded in the research"}>
                    from research
                  </Badge>
                ) : (
                  <span className="text-caption text-ink-subtle">Your question</span>
                )}
                <button
                  type="button"
                  aria-label="Remove question"
                  title="Remove question"
                  onClick={() => {
                    setQuestions(list.filter((_, i) => i !== index));
                    setUnsaved(true);
                  }}
                  className={`flex h-9 w-9 shrink-0 items-center justify-center rounded text-ink-subtle transition hover:bg-status-danger-bg hover:text-status-danger ${FOCUS_RING}`}
                >
                  <Trash2 size={13} />
                </button>
              </div>
              <Textarea
                rows={2}
                className="mt-2"
                value={question.text}
                onChange={(e) => edit(index, e.target.value)}
              />
              {/* The evidence, so a question that asserts more than its source
                  says is visible before it reaches the guest. */}
              {question.citations?.length ? (
                <p className="mt-1 text-caption text-ink-subtle">
                  “{question.citations[0].quote}”
                  {question.citations[0].source_title
                    ? ` — ${question.citations[0].source_title}`
                    : null}
                </p>
              ) : null}
            </div>
          ))}

          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              onClick={() => {
                setQuestions([
                  ...list,
                  { text: "", why: null, basis: "host", claim_ids: [] },
                ]);
                setUnsaved(true);
              }}
            >
              Add a question
            </Button>
            <Button
              onClick={() => {
                setError(null);
                save.mutate();
              }}
              disabled={save.isPending || !list.some((q) => q.text.trim())}
            >
              {save.isPending
                ? "Saving…"
                : unsaved
                  ? "Save questionnaire"
                  : "Saved"}
            </Button>
          </div>
        </div>
      ) : null}

      <div className="mt-4 flex items-center justify-between gap-3">
        {url ? (
          <>
            <code className="min-w-0 flex-1 truncate rounded border border-line bg-surface-muted px-2 py-1.5 text-caption text-ink-muted">
              {url}
            </code>
            <Button
              variant="ghost"
              onClick={() => {
                navigator.clipboard?.writeText(url);
                setCopied(true);
                window.setTimeout(() => setCopied(false), 1500);
              }}
            >
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? "Copied" : "Copy"}
            </Button>
            <Button variant="danger" onClick={() => revoke.mutate()}>
              Revoke
            </Button>
          </>
        ) : (
          <>
            <p className="text-caption text-ink-subtle">
              {approved
                ? "Ready to send."
                : "Save a questionnaire to create the link."}
            </p>
            <Button
              variant="ghost"
              onClick={() => {
                setError(null);
                create.mutate();
              }}
              disabled={!approved || create.isPending}
            >
              Create link
            </Button>
          </>
        )}
      </div>
      {url && unsaved ? (
        <p className="mt-2 text-caption text-status-warning">
          Unsaved edits. Save to change what the guest sees.
        </p>
      ) : null}
    </div>
  );
}
