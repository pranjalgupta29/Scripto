"""Request and response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CreateEpisodeRequest(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    guest_name: str = Field(min_length=1, max_length=512)
    # Name alone is rejected: a wrong identity poisons everything downstream.
    disambiguator: str = Field(
        min_length=2,
        max_length=1024,
        description="LinkedIn URL, firm name, or X handle. Required.",
    )


class Candidate(BaseModel):
    name: str
    headline: str | None = None
    employer: str | None = None
    photo_url: str | None = None
    evidence_urls: list[str] = []
    confidence: float = 0.0
    reasoning: str | None = None


class CandidatesResponse(BaseModel):
    job_id: uuid.UUID | None = None
    candidates: list[Candidate] = []


class ConfirmGuestRequest(BaseModel):
    candidate_index: int = Field(ge=0)


class SourceOut(BaseModel):
    id: uuid.UUID
    type: str
    url: str | None
    title: str | None
    author: str | None
    published_at: datetime | None
    status: str
    error: str | None
    added_by: str
    # Which research this source serves: "guest", or "topic" for the topic brief.
    subject: str = "guest"
    # For topic research: the host topic this source was found for.
    topic: str | None = None
    # Identity gate verdict for a discovered page: "ok", "mismatch", or None.
    identity: str | None = None

    model_config = {"from_attributes": True}


class PrepLinkOut(BaseModel):
    """The link the host sends the guest."""

    token: str
    path: str
    created_at: datetime | None = None


PREP_STYLES = Literal["conversational", "formal", "contrarian", "educational"]


class PrepQuestion(BaseModel):
    """One question on the guest's questionnaire, as approved by the host."""

    text: str
    why: str | None = None
    basis: str = "host"  # research | format | host
    claim_ids: list[str] = []


class PrepQuestionSuggestion(PrepQuestion):
    citations: list[CitationOut] = []


class PrepQuestionsResponse(BaseModel):
    episode_id: uuid.UUID
    style: str
    questions: list[PrepQuestionSuggestion]


class PrepQuestionsRequest(BaseModel):
    """What the host approves. Questions are theirs by the time they are saved."""

    style: PREP_STYLES = "conversational"
    questions: list[PrepQuestion] = Field(min_length=1, max_length=12)


class PrepPageOut(BaseModel):
    """What the guest sees: enough to know the link is genuine, no more."""

    episode_title: str
    guest_name: str
    style: str = "conversational"
    questions: list[PrepQuestion] = []
    submitted: int = 0


class PrepAnswer(BaseModel):
    question: str
    answer: str


class PrepAnswersRequest(BaseModel):
    answers: list[PrepAnswer] = Field(default_factory=list, max_length=12)


class PrepNotesRequest(BaseModel):
    bio: str | None = None
    links: list[str] = Field(default_factory=list, max_length=10)
    want_to_discuss: str | None = None
    avoid: str | None = None


class AddSourceRequest(BaseModel):
    url: str | None = None
    text: str | None = None
    title: str | None = None


class JobProgress(BaseModel):
    total: int
    finished: int
    pending: int
    by_state: dict[str, int]
    by_kind: dict[str, dict[str, int]]
    # What the progress bar shows: sources, not jobs.
    sources_total: int = 0
    sources_read: int = 0
    sources_analysed: int = 0
    stage: str | None = None


class EntityOut(BaseModel):
    id: uuid.UUID
    name: str
    headline: str | None
    employer: str | None
    photo_url: str | None

    model_config = {"from_attributes": True}


class EpisodeOut(BaseModel):
    id: uuid.UUID
    title: str
    guest_name: str
    disambiguator: str
    status: str
    coverage_mode: str | None
    coverage_detail: dict[str, Any] | None
    created_at: datetime
    guest: EntityOut | None = None
    sources: list[SourceOut] = []
    progress: JobProgress | None = None
    candidates: list[Candidate] = []


class CitationOut(BaseModel):
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    source_title: str | None
    source_url: str | None
    quote: str
    start_ms: int | None = None
    end_ms: int | None = None


class DossierItemOut(BaseModel):
    id: uuid.UUID
    section: str
    ordinal: int
    text: str
    item_date: datetime | None
    citations: list[CitationOut] = []


class DossierSection(BaseModel):
    section: str
    items: list[DossierItemOut]


class DossierResponse(BaseModel):
    episode_id: uuid.UUID
    coverage_mode: str | None
    coverage_detail: dict[str, Any] | None
    sections: list[DossierSection]


class TopicsRequest(BaseModel):
    # The user supplies the topics. We research them; we do not suggest them.
    topics: list[str] = Field(min_length=3, max_length=6)


class TopicOut(BaseModel):
    id: uuid.UUID
    ordinal: int
    text: str

    model_config = {"from_attributes": True}


class TopicSuggestion(BaseModel):
    text: str
    why: str | None = None
    # "research": rests on cited claims, shown with evidence.
    # "title": implied by the episode title only; nothing researched backs it.
    basis: Literal["research", "title"]
    claim_ids: list[str] = []
    citations: list[CitationOut] = []


class SuggestTopicsResponse(BaseModel):
    episode_id: uuid.UUID
    coverage_mode: str | None
    suggestions: list[TopicSuggestion]


class CreateScriptRequest(BaseModel):
    style_preset: Literal["formal", "conversational", "contrarian", "educational"]
    voice_sample: str | None = Field(
        default=None, description="Optional past-episode transcript to match voice."
    )
    duration_minutes: int = Field(
        default=60, ge=10, le=240, description="Planned interview length in minutes."
    )
    optimize_order: bool = Field(
        default=True,
        description="Let the generator order topics for the best conversational arc.",
    )
    include_bonus: bool = Field(
        default=True, description="Also suggest backup topics for when time allows."
    )
    feedback: str | None = Field(
        default=None,
        max_length=2000,
        description="What should change compared with the current version.",
    )
    use_guest_prep: bool = Field(
        default=False,
        description=(
            "Let what the guest sent through the prep link shape emphasis, order "
            "and what to avoid. Ignored when the guest has sent nothing."
        ),
    )


class SegmentOut(BaseModel):
    id: uuid.UUID
    ordinal: int
    segment_type: str = "topic"
    title: str | None = None
    start_minute: int | None = None
    planned_minutes: int | None = None
    topic_id: uuid.UUID | None
    transition_in: str | None = None
    host_script: str | None = None
    question: str
    deeper_questions: list[Any] = []
    rationale: str | None
    expected_direction: str | None
    followups: list[Any]
    risk_flags: list[Any]
    flagged_unsourced: bool = False
    edited_by_user: bool
    citations: list[CitationOut] = []


class ScriptResponse(BaseModel):
    id: uuid.UUID
    episode_id: uuid.UUID
    style_preset: str
    model_version: str
    duration_minutes: int | None = None
    feedback: str | None = None
    parent_script_id: uuid.UUID | None = None
    guest_prep_used: bool = False
    created_at: datetime
    segments: list[SegmentOut]


class PatchSegmentRequest(BaseModel):
    title: str | None = None
    transition_in: str | None = None
    host_script: str | None = None
    question: str | None = None
    deeper_questions: list[str] | None = None
    rationale: str | None = None
    expected_direction: str | None = None
    followups: list[str] | None = None
    risk_flags: list[str] | None = None


class JobAccepted(BaseModel):
    job_id: uuid.UUID | None
    episode_id: uuid.UUID
    status: str
