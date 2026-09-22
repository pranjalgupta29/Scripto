"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { useState } from "react";

import { ApiError, api } from "@/lib/api";
import { Badge, Button, Card, Textarea } from "@/components/ui";

/**
 * The guest's page. No account, no sign-in, and none of the host's research is
 * visible here: it only takes material in. The questions were written by the
 * host from their research before this link existed.
 */
export default function PrepPage({ params }: { params: { token: string } }) {
  const { token } = params;
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [extra, setExtra] = useState("");
  const [links, setLinks] = useState("");

  const page = useQuery({
    queryKey: ["prep", token],
    queryFn: () => api.getPrepPage(token),
    retry: false,
  });

  const questions = page.data?.questions ?? [];

  const submitAnswers = useMutation({
    mutationFn: () =>
      api.submitPrepAnswers(
        token,
        questions
          .map((q, i) => ({ question: q.text, answer: (answers[i] ?? "").trim() }))
          .filter((a) => a.answer),
      ),
    onSuccess: () => {
      setSent(true);
      setAnswers({});
      page.refetch();
    },
    onError: (e: ApiError) => setError(e.message),
  });

  const submitExtra = useMutation({
    mutationFn: () =>
      api.submitPrepNotes(token, {
        bio: extra.trim() || undefined,
        links: links
          .split(/[\n,]/)
          .map((l) => l.trim())
          .filter(Boolean),
      }),
    onSuccess: () => {
      setSent(true);
      setExtra("");
      setLinks("");
      page.refetch();
    },
    onError: (e: ApiError) => setError(e.message),
  });

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadPrepFile(token, file),
    onSuccess: () => {
      setSent(true);
      page.refetch();
    },
    onError: (e: ApiError) => setError(e.message),
  });

  if (page.isLoading) return null;

  if (page.isError) {
    return (
      <div className="mx-auto max-w-xl">
        <Card>
          <h1 className="font-serif text-heading font-semibold text-ink">
            This link is not active
          </h1>
          <p className="mt-2 text-body text-ink-muted">
            It may have been revoked, or the address may be incomplete. Ask your
            host for a new one.
          </p>
        </Card>
      </div>
    );
  }

  const data = page.data!;
  const answered = questions.filter((_, i) => (answers[i] ?? "").trim()).length;

  return (
    <div className="mx-auto max-w-xl">
      <h1 className="font-serif text-display font-semibold text-ink">
        Before we record — what would you like to talk about?
      </h1>
      <p className="mt-2 text-body text-ink-muted">
        You are a guest on{" "}
        <span className="font-medium text-ink">{data.episode_title}</span>. These are here
        to shape the conversation, not to preview it — your host will bring their
        own questions on the day. Answer what you like, skip what you do not.
      </p>

      {sent ? (
        <div className="mt-4">
          <Badge tone="success" className="px-3 py-2 text-body">
            Thank you — that reached your host. You can keep adding more below.
          </Badge>
        </div>
      ) : null}
      {error ? <p className="mt-4 text-body text-status-danger">{error}</p> : null}

      {questions.length ? (
        <Card className="mt-6">
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              setError(null);
              submitAnswers.mutate();
            }}
          >
            {questions.map((question, index) => (
              <label key={index} className="block">
                <span className="text-body text-ink">{question.text}</span>
                <Textarea
                  rows={3}
                  className="mt-1"
                  value={answers[index] ?? ""}
                  onChange={(e) =>
                    setAnswers({ ...answers, [index]: e.target.value })
                  }
                />
              </label>
            ))}
            <Button
              type="submit"
              disabled={submitAnswers.isPending || answered === 0}
            >
              {submitAnswers.isPending ? "Sending…" : "Send answers"}
            </Button>
          </form>
        </Card>
      ) : null}

      <Card className="mt-4">
        <h2 className="text-heading font-semibold text-ink">Attach a file</h2>
        <p className="mt-1 text-caption text-ink-subtle">
          A CV, a bio, a talk transcript. PDF, Word, .txt or .md. If your
          LinkedIn profile is the best summary, open it, choose More → Save to
          PDF, and send that.
        </p>
        <label className="mt-3 flex cursor-pointer items-center justify-center gap-2 rounded border border-dashed border-line px-3 py-4 text-center text-body text-ink-muted transition hover:border-line-strong">
          <input
            type="file"
            accept=".pdf,.docx,.txt,.md"
            className="hidden"
            disabled={upload.isPending}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              setError(null);
              upload.mutate(file);
            }}
          />
          <Upload size={15} className="shrink-0" />
          {upload.isPending ? "Sending…" : "Choose a file"}
        </label>
      </Card>

      <Card className="mt-4">
        <h2 className="text-heading font-semibold text-ink">Anything else</h2>
        <form
          className="mt-3 space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            submitExtra.mutate();
          }}
        >
          <label className="block">
            <span className="text-caption font-medium text-ink-muted">
              Something they did not ask about, or would rather you avoided
            </span>
            <Textarea
              rows={3}
              className="mt-1"
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-caption font-medium text-ink-muted">
              Links worth reading — past interviews, essays, talks (one per line)
            </span>
            <Textarea
              rows={3}
              className="mt-1"
              value={links}
              onChange={(e) => setLinks(e.target.value)}
            />
          </label>
          <Button
            type="submit"
            variant="ghost"
            disabled={submitExtra.isPending || (!extra.trim() && !links.trim())}
          >
            {submitExtra.isPending ? "Sending…" : "Send"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
