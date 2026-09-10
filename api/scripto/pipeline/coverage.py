"""5.7b Coverage check and thin footprint mode.

Most guests at small companies have a thin public footprint. That is the common
case, and an empty dossier makes the product look broken. So we score coverage
and tell the user plainly what was found and what was missing.

The topic brief is not a second system: we create a `topic` entity and run the
exact same discover/fetch/chunk/extract/cluster pipeline against it. Only the
composer differs.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scripto.config import settings
from scripto.jobs import queue
from scripto.jobs.registry import register
from scripto.models import (
    Chunk,
    Claim,
    ClaimCluster,
    Entity,
    Episode,
    EpisodeSource,
    Job,
    Source,
    Topic,
)

# A long-form appearance is the strongest single signal that a guest has said
# enough in public to build an episode around.
LONG_FORM_TYPES = {"youtube"}
LONG_FORM_MIN_CHARS = 8000


def score_coverage(db: Session, episode: Episode) -> dict:
    subject_id = episode.guest_entity_id

    sources = list(
        db.scalars(
            select(Source)
            .join(EpisodeSource, EpisodeSource.source_id == Source.id)
            .where(
                EpisodeSource.episode_id == episode.id,
                EpisodeSource.removed_at.is_(None),
            )
        )
    )
    parsed = [s for s in sources if s.status == "parsed"]
    failed = [s for s in sources if s.status == "failed"]

    clusters = (
        db.scalar(
            select(func.count(ClaimCluster.id)).where(
                ClaimCluster.subject_entity_id == subject_id
            )
        )
        or 0
    )
    claim_count = (
        db.scalar(select(func.count(Claim.id)).where(Claim.subject_entity_id == subject_id)) or 0
    )

    span = db.execute(
        select(func.min(Claim.claim_date), func.max(Claim.claim_date)).where(
            Claim.subject_entity_id == subject_id, Claim.claim_date.isnot(None)
        )
    ).first()
    first_seen, last_seen = (span or (None, None))

    has_long_form = False
    for source in parsed:
        if source.type in LONG_FORM_TYPES:
            has_long_form = True
            break
        total = (
            db.scalar(
                select(func.coalesce(func.sum(func.length(Chunk.text)), 0)).where(
                    Chunk.source_id == source.id
                )
            )
            or 0
        )
        if total >= LONG_FORM_MIN_CHARS:
            has_long_form = True
            break

    mode = _mode(len(parsed), clusters, has_long_form)

    return {
        "mode": mode,
        "sources_parsed": len(parsed),
        "sources_failed": len(failed),
        "sources_total": len(sources),
        "claim_count": claim_count,
        "cluster_count": clusters,
        "has_long_form": has_long_form,
        "date_span": {
            "first": first_seen.isoformat() if first_seen else None,
            "last": last_seen.isoformat() if last_seen else None,
        },
        "missing": _missing(mode, len(parsed), has_long_form),
    }


def _mode(parsed: int, clusters: int, has_long_form: bool) -> str:
    if (
        parsed >= settings.coverage_rich_min_sources
        and clusters >= settings.coverage_rich_min_clusters
        and has_long_form
    ):
        return "rich"
    if parsed >= settings.coverage_thin_min_sources:
        return "thin"
    return "sparse"


def _missing(mode: str, parsed: int, has_long_form: bool) -> list[str]:
    """What to ask the user for. Specific beats generic."""
    if mode == "rich":
        return []
    asks = []
    if not has_long_form:
        asks.append("a recording or transcript of a past talk, webinar or podcast appearance")
    if parsed < settings.coverage_rich_min_sources:
        asks.append("their CV, bio or internal notes about them")
        asks.append("links to anything they have written")
    return asks


@register("coverage_check")
def run_coverage(db: Session, job: Job) -> None:
    """Score coverage once ingestion for this episode has settled."""
    episode = db.get(Episode, job.episode_id)
    if episode is None:
        return

    # Wait for ingestion to finish before judging coverage, otherwise an
    # episode looks sparse simply because its fetches have not run yet.
    outstanding = db.scalar(
        select(func.count(Job.id)).where(
            Job.episode_id == episode.id,
            Job.kind.in_(["fetch_source", "parse_source", "extract_claims", "embed"]),
            Job.state.in_(["queued", "running"]),
        )
    )
    if outstanding:
        queue.enqueue(
            db,
            kind="coverage_check",
            episode_id=episode.id,
            user_id=job.user_id,
            payload={},
            delay_seconds=10,
        )
        return

    detail = score_coverage(db, episode)
    episode.coverage_mode = detail["mode"]
    episode.coverage_detail = {**(episode.coverage_detail or {}), **detail}
    db.flush()

    # Thin and sparse guests lean on topic material, so research the topics the
    # user gave us through the same pipeline.
    if detail["mode"] in ("thin", "sparse"):
        _kick_topic_research(db, episode, job)

    # The idempotency key must change when the inputs change, otherwise the
    # first build wins forever -- including a build that ran before extraction
    # produced anything. Keying on the claim count keeps repeat coverage checks
    # cheap while still rebuilding whenever new claims land.
    queue.enqueue(
        db,
        kind="build_dossier",
        episode_id=episode.id,
        user_id=job.user_id,
        payload={},
        idempotency_key=(
            f"dossier:{episode.id}:{detail['mode']}:{detail['claim_count']}"
        ),
    )


def _kick_topic_research(db: Session, episode: Episode, job: Job) -> None:
    """Create a topic entity and run the same discover pipeline against it."""
    if episode.topic_entity_id is not None:
        return

    topics = list(
        db.scalars(select(Topic).where(Topic.episode_id == episode.id).order_by(Topic.ordinal))
    )
    if not topics:
        return  # the user has not entered topics yet; rerun when they do

    label = "; ".join(t.text for t in topics[:3])
    entity = Entity(
        id=uuid.uuid4(),
        type="topic",
        name=label,
        aliases=[t.text for t in topics],
        external_ids={},
    )
    db.add(entity)
    db.flush()

    episode.topic_entity_id = entity.id
    db.flush()

    queue.enqueue(
        db,
        kind="discover",
        episode_id=episode.id,
        user_id=job.user_id,
        payload={"entity_id": str(entity.id), "mode": "topic"},
        idempotency_key=f"discover-topic:{episode.id}",
    )
