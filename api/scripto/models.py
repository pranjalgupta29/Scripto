"""SQLAlchemy models. Schema follows the v1 build spec section 4.

Two structural decisions carry the economics of the product:
  * `sources` are GLOBAL, deduped on canonical_url and checksum. Two users
    researching the same guest never refetch or reparse the same page.
  * `claims` are scoped to the ENTITY, not the episode. A second episode with
    the same guest reuses every claim already extracted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from scripto.config import settings
from scripto.db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)


# --------------------------------------------------------------------------
# entities
# --------------------------------------------------------------------------


class Entity(Base, TimestampMixin):
    """A person, company or topic.

    Topic entities let the same pipeline research an industry or subject with
    no new machinery, only a different composer.
    """

    __tablename__ = "entities"
    __table_args__ = (
        CheckConstraint("type IN ('person','company','topic')", name="ck_entity_type"),
        Index("ix_entities_type_name", "type", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    external_ids: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # denormalised display fields captured at identification time
    headline: Mapped[str | None] = mapped_column(String(512))
    employer: Mapped[str | None] = mapped_column(String(512))
    photo_url: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------
# episodes
# --------------------------------------------------------------------------

EPISODE_STATUSES = ("identifying", "ingesting", "dossier_ready", "script_ready")


class Episode(Base, TimestampMixin):
    __tablename__ = "episodes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('identifying','ingesting','dossier_ready','script_ready')",
            name="ck_episode_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    guest_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("entities.id"))
    topic_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("entities.id"))
    status: Mapped[str] = mapped_column(String(32), default="identifying", nullable=False)

    # the raw inputs the user gave us, kept for the identify step and for audit
    guest_name: Mapped[str] = mapped_column(String(512), nullable=False)
    disambiguator: Mapped[str] = mapped_column(String(1024), nullable=False)

    # set by the coverage check: rich | thin | sparse
    coverage_mode: Mapped[str | None] = mapped_column(String(16))
    coverage_detail: Mapped[dict | None] = mapped_column(JSONB)

    guest: Mapped[Entity | None] = relationship(foreign_keys=[guest_entity_id], lazy="joined")


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------

SOURCE_TYPES = ("web_article", "youtube", "pdf", "profile", "user_pasted")
SOURCE_STATUSES = ("pending", "fetched", "parsed", "failed")


class Source(Base):
    """Global, not per-episode. Deduped on canonical_url and on checksum."""

    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("canonical_url", name="uq_sources_canonical_url"),
        UniqueConstraint("checksum", name="uq_sources_checksum"),
        CheckConstraint(
            "type IN ('web_article','youtube','pdf','profile','user_pasted')",
            name="ck_source_type",
        ),
        CheckConstraint(
            "status IN ('pending','fetched','parsed','failed')", name="ck_source_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(512))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blob_ref: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class EpisodeSource(Base):
    """Join table. Removing a source is per-episode, it never deletes the source."""

    __tablename__ = "episode_sources"
    __table_args__ = (UniqueConstraint("episode_id", "source_id", name="uq_episode_source"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    episode_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    added_by: Mapped[str] = mapped_column(String(16), default="system", nullable=False)  # system|user
    # Which research this source serves: the guest, or the topic brief. NULL is
    # treated as the guest (host-added sources, and rows from before this existed).
    subject_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL")
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


# --------------------------------------------------------------------------
# chunks
# --------------------------------------------------------------------------


class Chunk(Base):
    """Offsets for text sources, ms for AV sources. Exactly one pair is populated.

    A chunk that cannot point back to a precise location in its source is
    useless and is not stored.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "ordinal", name="uq_chunk_source_ordinal"),
        CheckConstraint(
            "(start_offset IS NOT NULL AND end_offset IS NOT NULL)"
            " OR (start_ms IS NOT NULL AND end_ms IS NOT NULL)",
            name="ck_chunk_has_position",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int | None] = mapped_column(Integer)
    end_offset: Mapped[int | None] = mapped_column(Integer)
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    speaker: Mapped[str | None] = mapped_column(String(255))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dim))


# --------------------------------------------------------------------------
# claims
# --------------------------------------------------------------------------

CLAIM_KINDS = ("biographical", "opinion", "fact", "anecdote", "prediction")


class Claim(Base, TimestampMixin):
    """Scoped to the entity, not the episode."""

    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('biographical','opinion','fact','anecdote','prediction')",
            name="ck_claim_kind",
        ),
        Index("ix_claims_subject_kind", "subject_entity_id", "kind"),
        UniqueConstraint(
            "chunk_id", "text_hash", "extractor_version", name="uq_claim_chunk_text_version"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    claim_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("claim_clusters.id", ondelete="SET NULL"), index=True
    )
    # Holds "provider:extract_model/compose_model", which is easily 60+ chars.
    extractor_version: Mapped[str] = mapped_column(String(255), nullable=False)

    # the exact span inside the chunk that this claim rests on. Validated in
    # code: a claim whose span does not resolve inside its chunk is discarded.
    span_start: Mapped[int] = mapped_column(Integer, nullable=False)
    span_end: Mapped[int] = mapped_column(Integer, nullable=False)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dim))


class ClaimCluster(Base):
    """source_count is what powers 'he has said this in 9 places'."""

    __tablename__ = "claim_clusters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    subject_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    canonical_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


# --------------------------------------------------------------------------
# topics, dossier, scripts
# --------------------------------------------------------------------------


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("episode_id", "ordinal", name="uq_topic_episode_ordinal"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    episode_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class DossierItem(Base):
    """One generated sentence/bullet in a dossier section.

    Every item must carry the claim ids it rests on. The verification pass
    drops or flags any item with none.
    """

    __tablename__ = "dossier_items"
    __table_args__ = (Index("ix_dossier_episode_section", "episode_id", "section", "ordinal"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    episode_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    item_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Clusters are derived data and get rebuilt wholesale; a dossier item
    # pointing at an old cluster must not block that rebuild.
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("claim_clusters.id", ondelete="SET NULL")
    )
    flagged_unsourced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Script(Base, TimestampMixin):
    __tablename__ = "scripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    episode_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    style_preset: Mapped[str] = mapped_column(String(32), nullable=False)
    voice_sample_ref: Mapped[str | None] = mapped_column(Text)
    voice_descriptors: Mapped[dict | None] = mapped_column(JSONB)
    # "provider:extract_model/compose_model" -- the same long value that
    # overflowed claims.extractor_version at 32 characters.
    model_version: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    # Regeneration: the version this one revises, and what the host asked to
    # change. Earlier versions are kept, never overwritten.
    parent_script_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scripts.id", ondelete="SET NULL")
    )
    feedback: Mapped[str | None] = mapped_column(Text)


class ScriptSegment(Base):
    __tablename__ = "script_segments"
    __table_args__ = (
        UniqueConstraint("script_id", "ordinal", name="uq_segment_script_ordinal"),
        CheckConstraint(
            "segment_type IN ('opening','topic','bonus','closing')", name="ck_segment_type"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    script_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    expected_direction: Mapped[str | None] = mapped_column(Text)
    followups: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    risk_flags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    claim_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Run-of-show structure. A script is an ordered list of blocks: one opening,
    # one per topic, one closing, then optional backup ("bonus") topics that sit
    # outside the timeline. The clock is computed in code, never by the model.
    segment_type: Mapped[str] = mapped_column(
        String(16), default="topic", server_default="topic", nullable=False
    )
    title: Mapped[str | None] = mapped_column(Text)
    start_minute: Mapped[int | None] = mapped_column(Integer)
    planned_minutes: Mapped[int | None] = mapped_column(Integer)
    # Spoken bridge into this block (for the opening: the cold-open hook).
    transition_in: Mapped[str | None] = mapped_column(Text)
    # Words for the host to say (opening: guest intro; closing: wrap-up).
    host_script: Mapped[str | None] = mapped_column(Text)
    deeper_questions: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=sa_text("'[]'::jsonb"), nullable=False
    )
    # True when a block that should rest on research cites nothing. Shown to the
    # host rather than silently dropped, since removing a block breaks the clock.
    flagged_unsourced: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa_text("false"), nullable=False
    )


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('dossier_item','script_segment')", name="ck_citation_target"
        ),
        Index("ix_citations_target", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False
    )
    span_start: Mapped[int | None] = mapped_column(Integer)
    span_end: Mapped[int | None] = mapped_column(Integer)


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------

JOB_KINDS = (
    "identify",
    "discover",
    "fetch_source",
    "parse_source",
    "embed",
    "extract_claims",
    "cluster_claims",
    "coverage_check",
    "build_dossier",
    "generate_script",
)
JOB_STATES = ("queued", "running", "done", "failed", "dead")


class Job(Base):
    """user_id is on the job so the dequeue can be fair across users."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued','running','done','failed','dead')", name="ck_job_state"
        ),
        Index("ix_jobs_dequeue", "state", "next_attempt_at"),
        Index("ix_jobs_episode", "episode_id", "state"),
        Index("ix_jobs_user_running", "user_id", "state"),
        UniqueConstraint("idempotency_key", name="uq_job_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ProviderBudget(Base):
    """Hard ceiling on billable external calls, enforced in code.

    A vendor dashboard limit is a promise by someone else; this is a promise by
    us. When the count reaches the cap, the call is refused before it is made,
    so an overage cannot happen no matter what the pipeline does.
    """

    __tablename__ = "provider_budgets"
    __table_args__ = (UniqueConstraint("provider", "period", name="uq_budget_provider_period"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ProviderPacing(Base):
    """Last call time per provider, so workers can pace to a requests-per-minute
    ceiling collectively rather than each discovering the limit by being 429'd.

    Concurrency caps alone do not bound request rate: two workers making 2s
    calls sustain ~60 rpm, which most free tiers refuse.
    """

    __tablename__ = "provider_pacing"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_call_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class UsageCounter(Base):
    """Per user quotas. Cheap now, painful to retrofit."""

    __tablename__ = "usage_counters"
    __table_args__ = (UniqueConstraint("user_id", "day", "kind", name="uq_usage_user_day_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    day: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
