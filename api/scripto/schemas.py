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

    model_config = {"from_attributes": True}


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


class CreateScriptRequest(BaseModel):
    style_preset: Literal["formal", "conversational", "contrarian", "educational"]
    voice_sample: str | None = Field(
        default=None, description="Optional past-episode transcript to match voice."
    )


class SegmentOut(BaseModel):
    id: uuid.UUID
    ordinal: int
    topic_id: uuid.UUID | None
    question: str
    rationale: str | None
    expected_direction: str | None
    followups: list[Any]
    risk_flags: list[Any]
    edited_by_user: bool
    citations: list[CitationOut] = []


class ScriptResponse(BaseModel):
    id: uuid.UUID
    episode_id: uuid.UUID
    style_preset: str
    model_version: str
    created_at: datetime
    segments: list[SegmentOut]


class PatchSegmentRequest(BaseModel):
    question: str | None = None
    rationale: str | None = None
    expected_direction: str | None = None
    followups: list[str] | None = None
    risk_flags: list[str] | None = None


class JobAccepted(BaseModel):
    job_id: uuid.UUID | None
    episode_id: uuid.UUID
    status: str
