"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { Badge, Button, Card, CitationList, Input } from "@/components/ui";

/* ------------------------------------------------------------------ */
/* topics — the host's own, plus suggestions that say what backs them */
/* ------------------------------------------------------------------ */

const MAX_TOPICS = 6;

export function TopicsPanel({ episodeId }: { episodeId: string }) {
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
          <h2 className="text-heading font-semibold text-ink">Topics</h2>
          <p className="mt-1 text-ui text-ink-muted">
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
        {saved ? <Badge tone="success">Saved</Badge> : null}
      </div>
      {error ? <p className="mt-2 text-body text-status-danger">{error}</p> : null}

      {suggest.data ? (
        <div className="mt-5 border-t border-line pt-4">
          <p className="text-caption font-semibold uppercase tracking-wide text-ink-subtle">
            Suggested from the episode title and the research
          </p>
          {!suggest.data.suggestions.length ? (
            <p className="mt-2 text-body text-ink-subtle">No new suggestions.</p>
          ) : null}
          <ul className="mt-2 space-y-2">
            {suggest.data.suggestions.map((s) => {
              const added = alreadyHas(s.text);
              return (
                <li
                  key={s.text}
                  className="flex items-start justify-between gap-3 rounded-md border border-line p-3"
                >
                  <div className="min-w-0">
                    <p className="text-body font-medium text-ink">{s.text}</p>
                    {s.why ? (
                      <p className="mt-0.5 text-caption text-ink-subtle">{s.why}</p>
                    ) : null}
                    <Badge tone={s.basis === "research" ? "success" : "warning"} className="mt-1">
                      {s.basis === "research"
                        ? "Backed by research"
                        : "From the title only — not researched"}
                    </Badge>
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
            <p className="mt-2 text-caption text-ink-subtle">
              Six topics is the maximum. Remove one to add another.
            </p>
          ) : (
            <p className="mt-2 text-caption text-ink-subtle">
              Added suggestions still need saving.
            </p>
          )}
        </div>
      ) : null}
    </Card>
  );
}
