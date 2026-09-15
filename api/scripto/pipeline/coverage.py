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

import hashlib
import math

from sqlalchemy import func, or_, select
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
LONG_FORM_MIN_CHARS = 8000
# A long-form appearance means a video, or a long page that is an interview,
# podcast, talk or transcript. A long encyclopedia or profile page is not an
# appearance -- Wikipedia used to qualify just by being long.
LONG_FORM_HINTS = (
    "interview",
    "podcast",
    "transcript",
    "conversation",
    "episode",
    "talk",
    "keynote",
    "fireside",
    "q&a",
    "webinar",
)


def is_long_form(source: Source, chars: int) -> bool:
    if source.type == "youtube":
        return True
    label = f"{source.title or ''} {source.url or ''}".lower()
    return chars >= LONG_FORM_MIN_CHARS and any(hint in label for hint in LONG_FORM_HINTS)


def rich_min_sources() -> int:
    """How many readable guest sources "rich" needs.

    A share of what discovery fetches, not a fixed number. With a fixed 8 and a
    source limit of 8, one failed download made "rich" impossible, so even Satya
    Nadella and Andrew Huberman first came out "thin". The floor stops a tiny
    source limit from making almost anyone "rich".
    """
    share = math.ceil(settings.max_sources_per_episode * settings.coverage_rich_share)
    return max(settings.coverage_thin_min_sources + 1, share)


def score_coverage(db: Session, episode: Episode) -> dict:
    subject_id = episode.guest_entity_id

    sources = list(
        db.scalars(
            select(Source)
            .join(EpisodeSource, EpisodeSource.source_id == Source.id)
            .where(
                EpisodeSource.episode_id == episode.id,
                EpisodeSource.removed_at.is_(None),
                # Only the guest's own sources. Topic research articles once
                # counted here, which could push a thin guest to "rich" and hide
                # the topic brief built for them. NULL means the guest.
                or_(
                    EpisodeSource.subject_entity_id.is_(None),
                    EpisodeSource.subject_entity_id == subject_id,
                ),
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
        chars = 0
        if source.type != "youtube":
            chars = (
                db.scalar(
                    select(func.coalesce(func.sum(func.length(Chunk.text)), 0)).where(
                        Chunk.source_id == source.id
                    )
                )
                or 0
            )
        if is_long_form(source, chars):
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
        parsed >= rich_min_sources()
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
    if parsed < rich_min_sources():
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
    # Clustering is waited on too, so the dossier never uses stale clusters.
    outstanding = db.scalar(
        select(func.count(Job.id)).where(
            Job.episode_id == episode.id,
            Job.id != job.id,
            Job.kind.in_(
                ["discover", "fetch_source", "parse_source", "extract_claims", "embed", "cluster_claims"]
            ),
            Job.state.in_(["queued", "running"]),
        )
    )
    if outstanding:
        # Run this same job again later, rather than adding a new one each time.
        raise queue.Reschedule(10)

    detail = score_coverage(db, episode)
    episode.coverage_mode = detail["mode"]
    episode.coverage_detail = {**(episode.coverage_detail or {}), **detail}
    db.flush()

    # Research the host's topics for every episode, not only thin and sparse
    # guests (the host's call). Only topics not researched before cost anything.
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
        # Keyed on everything the dossier is built from, so claims arriving
        # later -- for the guest or for the topic brief -- always trigger a
        # rebuild, while a repeat check with nothing new stays a no-op.
        idempotency_key=(
            f"dossier:{episode.id}:{detail['mode']}:{detail['claim_count']}:"
            f"{_topic_claim_count(db, episode)}"
        ),
    )


def _topic_claim_count(db: Session, episode: Episode) -> int:
    if episode.topic_entity_id is None:
        return 0
    return (
        db.scalar(
            select(func.count(Claim.id)).where(Claim.subject_entity_id == episode.topic_entity_id)
        )
        or 0
    )


def _kick_topic_research(db: Session, episode: Episode, job: Job) -> None:
    """Research the host's topics through the same pipeline, once per topic.

    Every episode with topics gets this -- the host's call on 2026-09-15; the
    spec had limited it to thin and sparse guests. One topic entity per episode
    holds the current topics, and only topics not researched before trigger new
    searches, so editing topics costs a search round for the new ones only.
    """
    topics = [
        t.text
        for t in db.scalars(
            select(Topic).where(Topic.episode_id == episode.id).order_by(Topic.ordinal)
        )
    ]
    if not topics:
        return  # no topics yet; this runs again when the host saves some

    entity = db.get(Entity, episode.topic_entity_id) if episode.topic_entity_id else None
    if entity is None:
        entity = Entity(id=uuid.uuid4(), type="topic", name="", aliases=[], external_ids={})
        db.add(entity)
        db.flush()
        episode.topic_entity_id = entity.id

    researched = list((entity.external_ids or {}).get("researched_topics", []))
    new_topics = [t for t in topics if t not in researched]

    entity.name = "; ".join(topics[:3])
    entity.aliases = topics
    if not new_topics:
        db.flush()
        return

    entity.external_ids = {
        **(entity.external_ids or {}),
        "researched_topics": researched + new_topics,
    }
    db.flush()

    digest = hashlib.sha256("|".join(sorted(new_topics)).encode()).hexdigest()[:16]
    queue.enqueue(
        db,
        kind="discover",
        episode_id=episode.id,
        user_id=job.user_id,
        payload={"entity_id": str(entity.id), "mode": "topic", "topics": new_topics},
        idempotency_key=f"discover-topic:{episode.id}:{digest}",
    )
