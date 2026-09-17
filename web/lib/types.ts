export type EpisodeStatus =
  | "identifying"
  | "ingesting"
  | "dossier_ready"
  | "script_ready";

export type CoverageMode = "rich" | "thin" | "sparse";

export interface Candidate {
  name: string;
  headline: string | null;
  employer: string | null;
  photo_url: string | null;
  evidence_urls: string[];
  confidence: number;
  reasoning: string | null;
}

export interface SourceOut {
  id: string;
  type: string;
  url: string | null;
  title: string | null;
  author: string | null;
  published_at: string | null;
  status: "pending" | "fetched" | "parsed" | "failed";
  error: string | null;
  added_by: string;
  subject: "guest" | "topic";
  topic: string | null;
  // Identity gate verdict for a discovered page: "ok", "mismatch", or null.
  identity: string | null;
}

export interface PrepLink {
  token: string;
  path: string;
  created_at: string | null;
}

export interface PrepQuestion {
  text: string;
  why: string | null;
  basis: string;
  claim_ids: string[];
  citations?: Citation[];
}

export interface PrepQuestions {
  episode_id: string;
  style: string;
  questions: PrepQuestion[];
}

export interface PrepPage {
  episode_title: string;
  guest_name: string;
  style: string;
  questions: { text: string }[];
  submitted: number;
}

export interface JobProgress {
  total: number;
  finished: number;
  pending: number;
  by_state: Record<string, number>;
  by_kind: Record<string, Record<string, number>>;
  sources_total: number;
  sources_read: number;
  sources_analysed: number;
  stage: string | null;
}

export interface CoverageDetail {
  mode?: CoverageMode;
  sources_parsed?: number;
  sources_failed?: number;
  sources_total?: number;
  claim_count?: number;
  cluster_count?: number;
  has_long_form?: boolean;
  missing?: string[];
  date_span?: { first: string | null; last: string | null };
  topic_gaps?: string[];
}

export interface Episode {
  id: string;
  title: string;
  guest_name: string;
  disambiguator: string;
  status: EpisodeStatus;
  coverage_mode: CoverageMode | null;
  coverage_detail: CoverageDetail | null;
  created_at: string;
  guest: {
    id: string;
    name: string;
    headline: string | null;
    employer: string | null;
    photo_url: string | null;
  } | null;
  sources: SourceOut[];
  progress: JobProgress | null;
  candidates: Candidate[];
}

export interface Citation {
  chunk_id: string;
  source_id: string;
  source_title: string | null;
  source_url: string | null;
  quote: string;
  start_ms: number | null;
  end_ms: number | null;
}

export interface DossierItem {
  id: string;
  section: string;
  ordinal: number;
  text: string;
  item_date: string | null;
  citations: Citation[];
}

export interface Dossier {
  episode_id: string;
  coverage_mode: CoverageMode | null;
  coverage_detail: CoverageDetail | null;
  sections: { section: string; items: DossierItem[] }[];
}

export interface Topic {
  id: string;
  ordinal: number;
  text: string;
}

export type SegmentType = "opening" | "topic" | "closing" | "bonus";

export interface Segment {
  id: string;
  ordinal: number;
  segment_type: SegmentType;
  title: string | null;
  start_minute: number | null;
  planned_minutes: number | null;
  topic_id: string | null;
  transition_in: string | null;
  host_script: string | null;
  question: string;
  deeper_questions: string[];
  rationale: string | null;
  expected_direction: string | null;
  followups: string[];
  risk_flags: string[];
  flagged_unsourced: boolean;
  edited_by_user: boolean;
  citations: Citation[];
}

export interface Script {
  id: string;
  episode_id: string;
  style_preset: string;
  model_version: string;
  duration_minutes: number | null;
  feedback: string | null;
  parent_script_id: string | null;
  guest_prep_used: boolean;
  created_at: string;
  segments: Segment[];
}

export interface TopicSuggestion {
  text: string;
  why: string | null;
  basis: "research" | "title";
  claim_ids: string[];
  citations: Citation[];
}

export interface SuggestTopicsResponse {
  episode_id: string;
  coverage_mode: CoverageMode | null;
  suggestions: TopicSuggestion[];
}

export const SECTION_TITLES: Record<string, string> = {
  career_timeline: "Career timeline",
  recent_news: "Recent news",
  public_positions: "Public positions",
  already_covered: "Already covered elsewhere",
  unexplored_angles: "Unexplored angles",
  topic_brief: "Topic & industry brief",
};
