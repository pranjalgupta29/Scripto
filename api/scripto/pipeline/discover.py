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
from scripto.jobs.limits import provider_slot
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
                episode_id=episode.id, source_id=source.id, added_by=added_by
            )
        )
    elif link.removed_at is not None and added_by == "user":
        link.removed_at = None  # re-adding a source the user removed earlier
    db.flush()
    return source


@register("discover")
def run_discover(db: Session, job: Job) -> None:
    episode = db.get(Episode, job.episode_id)
    if episode is None or episode.guest_entity_id is None:
        return
    entity = db.get(Entity, episode.guest_entity_id)
    if entity is None:
        return

    search = get_search()
    seen: set[str] = set()
    attached: list[Source] = []

    with provider_slot("search"):
        for pattern in query_patterns(entity):
            if len(attached) >= settings.max_sources_per_episode:
                break
            try:
                results = search.search(pattern, limit=6)
            except Exception:
                continue  # one bad query never fails discovery
            for r in results:
                canonical = canonicalize_url(r.url)
                if not canonical or canonical in seen:
                    continue
                seen.add(canonical)
                source = attach_source(db, episode=episode, url=r.url, title=r.title)
                if source is not None:
                    attached.append(source)
                if len(attached) >= settings.max_sources_per_episode:
                    break

    for source in attached:
        if source.status == "pending":
            queue.enqueue(
                db,
                kind="fetch_source",
                episode_id=episode.id,
                user_id=job.user_id,
                payload={"source_id": str(source.id)},
                idempotency_key=f"fetch:{source.id}",
            )

    # Coverage runs after ingestion; it re-enqueues itself while work remains.
    queue.enqueue(
        db,
        kind="coverage_check",
        episode_id=episode.id,
        user_id=job.user_id,
        payload={},
        idempotency_key=f"coverage:{episode.id}",
        delay_seconds=5,
    )
