"use client";

import type {
  Candidate,
  Dossier,
  Episode,
  Script,
  SourceOut,
  SuggestTopicsResponse,
  Topic,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "scripto.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { raw?: boolean } = {},
): Promise<T> {
  const token = getToken();
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  signup: (email: string, password: string) =>
    request<{ access_token: string }>("/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  login: (email: string, password: string) =>
    request<{ access_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  listEpisodes: () => request<Episode[]>("/episodes"),

  createEpisode: (body: {
    title: string;
    guest_name: string;
    disambiguator: string;
  }) =>
    request<Episode>("/episodes", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getEpisode: (id: string) => request<Episode>(`/episodes/${id}`),

  identify: (id: string) =>
    request<{ candidates: Candidate[] }>(`/episodes/${id}/identify`, {
      method: "POST",
    }),

  confirmGuest: (id: string, candidate_index: number) =>
    request<Episode>(`/episodes/${id}/confirm-guest`, {
      method: "POST",
      body: JSON.stringify({ candidate_index }),
    }),

  addSource: (id: string, body: { url?: string; text?: string; title?: string }) =>
    request<SourceOut>(`/episodes/${id}/sources`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  removeSource: (id: string, sourceId: string) =>
    request<void>(`/episodes/${id}/sources/${sourceId}`, { method: "DELETE" }),

  getDossier: (id: string) => request<Dossier>(`/episodes/${id}/dossier`),

  setTopics: (id: string, topics: string[]) =>
    request<Topic[]>(`/episodes/${id}/topics`, {
      method: "POST",
      body: JSON.stringify({ topics }),
    }),

  getTopics: (id: string) => request<Topic[]>(`/episodes/${id}/topics`),

  suggestTopics: (id: string) =>
    request<SuggestTopicsResponse>(`/episodes/${id}/topics/suggest`, {
      method: "POST",
    }),

  createScript: (
    id: string,
    body: {
      style_preset: string;
      duration_minutes?: number;
      optimize_order?: boolean;
      include_bonus?: boolean;
      voice_sample?: string;
    },
  ) =>
    request<Script>(`/episodes/${id}/script`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getScript: (id: string) => request<Script>(`/episodes/${id}/script`),

  patchSegment: (
    scriptId: string,
    segmentId: string,
    body: Partial<{
      title: string;
      transition_in: string;
      host_script: string;
      deeper_questions: string[];
      question: string;
      rationale: string;
      expected_direction: string;
      followups: string[];
      risk_flags: string[];
    }>,
  ) =>
    request<unknown>(`/scripts/${scriptId}/segments/${segmentId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  exportUrl: (id: string) => `${BASE}/episodes/${id}/export?format=pdf`,

  downloadExport: async (id: string) => {
    const token = getToken();
    const response = await fetch(`${BASE}/episodes/${id}/export?format=pdf`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) throw new ApiError(response.status, "export failed");
    return response.blob();
  },
};
