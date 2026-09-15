"""5.2 Discover.

Generate a source list for a confirmed entity. Sources are global, so this
attaches existing rows where they already exist and only creates what is new.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from scripto.adapters import canonicalize_url, classify_url
from scripto.config import settings
from scripto.jobs import queue
from scripto.jobs.limits import BudgetExceeded, SlotUnavailable, provider_slot
from scripto.jobs.registry import register
from scripto.models import Entity, Episode, EpisodeSource, Job, Source
from scripto.search import get_search


def query_patterns(entity: Entity) -> list[str]:
    name = entity.name
    employer = entity.employer or ""
    patterns = [
        f'"{name}" {employer} news'.strip(),
        f'"{name}" interview',
        f'"{name}" podcast',
        f'"{name}" site:youtube.com',
        f'"{name}" blog OR substack OR essay',
        f'"{name}" bio {employer}'.strip(),
    ]
    if employer:
        # Public company guests: filings and earnings call mentions.
        patterns.append(f'"{name}" {employer} earnings call OR 10-K OR filing')
    return patterns


def attach_source(
    db: Session,
    *,
    episode: Episode,
    url: str,
    source_type: str | None = None,
    added_by: str = "system",
    title: str | None = None,
    subject_entity_id: uuid.UUID | None = None,
) -> Source | None:
    """Attach a URL to an episode, reusing the global source row if it exists."""
    canonical = canonicalize_url(url)
    if not canonical:
        return None

    source = db.scalar(select(Source).where(Source.canonical_url == canonical))
    if source is None:
        source = Source(
            id=uuid.uuid4(),
            type=source_type or classify_url(canonical),
            url=url,
            canonical_url=canonical,
            title=title,
            status="pending",
        )
        db.add(source)
        db.flush()

    link = db.scalar(
        select(EpisodeSource).where(
            EpisodeSource.episode_id == episode.id,
            EpisodeSource.source_id == source.id,
        )
    )
    if link is None:
        db.add(
            EpisodeSource(
                episode_id=episode.id,
                source_id=source.id,
                added_by=added_by,
                subject_entity_id=subject_entity_id,
            )
        )
    elif link.removed_at is not None and added_by == "user":
        link.removed_at = None  # re-adding a source the user removed earlier
    db.flush()
    return source


# Topic research: a handful of searches about the subjects themselves.
MAX_TOPICS_RESEARCHED = 6
MAX_TOPIC_QUERIES = 8


def topic_query_patterns(entity: Entity) -> list[str]:
    """Searches for a topic brief: the subject matter, not the guest."""
    topics = [t for t in (entity.aliases or []) if t] or [entity.name]
    patterns: list[str] = []
    for topic in topics[:MAX_TOPICS_RESEARCHED]:
        patterns.append(f"{topic} explained")
        patterns.append(f"{topic} latest research")
    return patterns[:MAX_TOPIC_QUERIES]


@register("discover")
def run_discover(db: Session, job: Job) -> None:
    episode = db.get(Episode, job.episode_id)
    if episode is None:
        return

    # A topic run researches the topic entity it was handed. It used to search
    # for the guest again, so the topic brief never got any material.
    topic_mode = job.payload.get("mode") == "topic"
    entity_id = job.payload.get("entity_id") if topic_mode else episode.guest_entity_id
    entity = db.get(Entity, uuid.UUID(str(entity_id))) if entity_id else None
    if entity is None:
        return

    patterns = topic_query_patterns(entity) if topic_mode else query_patterns(entity)
    search = get_search()
    seen: set[str] = set()
    attached: list[Source] = []

    # The cap is this run's allowance: guest research and topic research each
    # get their own, and neither counts sources the host adds by hand.
    for pattern in patterns:
        if len(attached) >= settings.max_sources_per_episode:
            break
        try:
            # One slot per search, so budgets and pacing count real calls.
            # This used to wrap the whole loop and charged ~7 searches as one.
            with provider_slot("search"):
                results = search.search(pattern, limit=6)
        except BudgetExceeded:
            break  # out of search budget: keep what was found so far
        except SlotUnavailable:
            raise  # provider busy: retry the whole run later
        except Exception:
            continue  # one bad query never fails discovery
        for r in results:
            canonical = canonicalize_url(r.url)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            source = attach_source(
                db, episode=episode, url=r.url, title=r.title, subject_entity_id=entity.id
            )
            if source is not None:
                attached.append(source)
            if len(attached) >= settings.max_sources_per_episode:
                break

    for source in attached:
        payload = {"source_id": str(source.id), "subject_entity_id": str(entity.id)}
        if source.status == "pending":
            queue.enqueue(
                db,
                kind="fetch_source",
                episode_id=episode.id,
                user_id=job.user_id,
                payload=payload,
                idempotency_key=f"fetch:{source.id}",
            )
        elif source.status == "parsed":
            # Already read for another episode: extract it for this subject.
            queue.enqueue(
                db,
                kind="extract_claims",
                episode_id=episode.id,
                user_id=job.user_id,
                payload=payload,
                idempotency_key=f"extract:{source.id}:{entity.id}:{source.checksum}",
            )

    # One coverage check per discovery run. A single shared key used to swallow
    # the topic run's check, so late claims never reached the dossier.
    queue.enqueue(
        db,
        kind="coverage_check",
        episode_id=episode.id,
        user_id=job.user_id,
        payload={},
        idempotency_key=f"coverage:{episode.id}:{job.id}",
        delay_seconds=5,
    )
