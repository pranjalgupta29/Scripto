"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, api, getToken, setToken } from "@/lib/api";
import type { Episode } from "@/lib/types";
import { Button, Card, Input } from "@/components/ui";

export default function HomePage() {
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => setAuthed(Boolean(getToken())), []);

  if (authed === null) return null;
  return authed ? (
    <EpisodeList onSignOut={() => setAuthed(false)} />
  ) : (
    <AuthPanel onAuthed={() => setAuthed(true)} />
  );
}

function AuthPanel({ onAuthed }: { onAuthed: () => void }) {
  const [mode, setMode] = useState<"login" | "signup">("signup");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = useMutation({
    mutationFn: () =>
      mode === "signup"
        ? api.signup(email, password)
        : api.login(email, password),
    onSuccess: (data) => {
      setToken(data.access_token);
      onAuthed();
    },
    onError: (e: ApiError) => setError(e.message),
  });

  return (
    <div className="mx-auto max-w-md">
      <h1 className="text-2xl font-semibold tracking-tight">
        Research your next guest
      </h1>
      <p className="mt-2 text-sm text-black/60">
        Every line in the dossier and the script links back to a source you can open.
      </p>

      <Card className="mt-6">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            submit.mutate();
          }}
          className="space-y-3"
        >
          <Input
            type="email"
            required
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Input
            type="password"
            required
            minLength={8}
            placeholder="password (8+ characters)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {error ? <p className="text-sm text-red-700">{error}</p> : null}
          <Button type="submit" disabled={submit.isPending} className="w-full">
            {submit.isPending
              ? "…"
              : mode === "signup"
                ? "Create account"
                : "Sign in"}
          </Button>
        </form>
        <button
          onClick={() => {
            setMode(mode === "signup" ? "login" : "signup");
            setError(null);
          }}
          className="mt-3 w-full text-xs text-black/50 hover:text-black"
        >
          {mode === "signup"
            ? "Already have an account? Sign in"
            : "Need an account? Sign up"}
        </button>
      </Card>
    </div>
  );
}

function EpisodeList({ onSignOut }: { onSignOut: () => void }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const episodes = useQuery({
    queryKey: ["episodes"],
    queryFn: api.listEpisodes,
  });

  const [title, setTitle] = useState("");
  const [guestName, setGuestName] = useState("");
  const [disambiguator, setDisambiguator] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api.createEpisode({
        title,
        guest_name: guestName,
        disambiguator,
      }),
    onSuccess: (episode: Episode) => {
      queryClient.invalidateQueries({ queryKey: ["episodes"] });
      router.push(`/episodes/${episode.id}`);
    },
    onError: (e: ApiError) => setError(e.message),
  });

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Episodes</h1>
          <p className="mt-1 text-sm text-black/60">
            One episode, one guest, one script.
          </p>
        </div>
        <Button
          variant="ghost"
          onClick={() => {
            setToken(null);
            onSignOut();
          }}
        >
          Sign out
        </Button>
      </div>

      <Card>
        <h2 className="text-sm font-semibold">New episode</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            create.mutate();
          }}
          className="mt-3 space-y-3"
        >
          <Input
            required
            placeholder="Episode title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <div className="grid gap-3 sm:grid-cols-2">
            <Input
              required
              placeholder="Guest name"
              value={guestName}
              onChange={(e) => setGuestName(e.target.value)}
            />
            <Input
              required
              minLength={2}
              placeholder="LinkedIn URL, firm, or @handle"
              value={disambiguator}
              onChange={(e) => setDisambiguator(e.target.value)}
            />
          </div>
          <p className="text-xs text-black/50">
            A disambiguator is required — a name alone identifies the wrong person
            often enough to poison the whole dossier.
          </p>
          {error ? <p className="text-sm text-red-700">{error}</p> : null}
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create episode"}
          </Button>
        </form>
      </Card>

      <div className="space-y-2">
        {episodes.isLoading ? (
          <p className="text-sm text-black/50">Loading…</p>
        ) : episodes.data?.length ? (
          episodes.data.map((episode) => (
            <button
              key={episode.id}
              onClick={() => router.push(`/episodes/${episode.id}`)}
              className="flex w-full items-center justify-between rounded-lg border border-black/10 bg-white px-5 py-4 text-left transition hover:border-black/25"
            >
              <div>
                <p className="font-medium">{episode.title}</p>
                <p className="text-sm text-black/55">
                  {episode.guest?.name ?? episode.guest_name}
                  {episode.guest?.employer ? ` · ${episode.guest.employer}` : ""}
                </p>
              </div>
              <div className="text-right">
                <span className="text-xs text-black/50">{episode.status}</span>
                {episode.coverage_mode && episode.coverage_mode !== "rich" ? (
                  <p className="text-xs text-amber-700">
                    {episode.coverage_mode} coverage
                  </p>
                ) : null}
              </div>
            </button>
          ))
        ) : (
          <p className="text-sm text-black/50">No episodes yet.</p>
        )}
      </div>
    </div>
  );
}
