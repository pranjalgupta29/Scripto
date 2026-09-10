"""5.3 Fetch and parse.

The raw payload is stored before parsing, because everything gets reparsed many
times as extraction improves and refetching is slow and sometimes impossible.
Parse reads only from blob storage, which is what makes reparsing network-free.

A failed source never fails the episode: it is marked failed, surfaced in the
UI, and the run continues.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from scripto.adapters import FetchError, get_adapter
from scripto.blob import checksum, get_blob
from scripto.jobs import queue
from scripto.jobs.limits import provider_slot
from scripto.jobs.registry import register
from scripto.models import Chunk, Episode, Job, Source
from scripto.pipeline.chunking import chunk_parsed_source


@register("fetch_source")
def run_fetch(db: Session, job: Job) -> None:
    source = db.get(Source, uuid.UUID(job.payload["source_id"]))
    if source is None:
        return

    # Idempotent: a retry after a successful fetch skips straight to parsing.
    if source.blob_ref and get_blob().exists(source.blob_ref):
        _enqueue_parse(db, job, source)
        return

    adapter = get_adapter(source.type)
    try:
        with provider_slot("fetch"):
            raw = adapter.fetch(source.url or "")
    except FetchError as exc:
        _mark_failed(db, source, str(exc))
        return
    except Exception as exc:
        _mark_failed(db, source, f"{type(exc).__name__}: {exc}")
        return

    digest = checksum(raw)

    # Global dedupe on content: two URLs serving identical bytes share one
    # parse and one set of chunks.
    twin = db.scalar(
        select(Source).where(Source.checksum == digest, Source.id != source.id)
    )
    if twin is not None and twin.status == "parsed":
        source.status = "failed"
        source.error = f"duplicate content of source {twin.id}"
        source.fetched_at = datetime.now(timezone.utc)
        db.flush()
        return

    source.blob_ref = get_blob().put(digest, raw)
    source.checksum = digest
    source.fetched_at = datetime.now(timezone.utc)
    source.status = "fetched"
    source.error = None
    db.flush()

    _enqueue_parse(db, job, source)


def _enqueue_parse(db: Session, job: Job, source: Source) -> None:
    queue.enqueue(
        db,
        kind="parse_source",
        episode_id=job.episode_id,
        user_id=job.user_id,
        payload={"source_id": str(source.id)},
        idempotency_key=f"parse:{source.id}:{source.checksum}",
    )


def _mark_failed(db: Session, source: Source, error: str) -> None:
    source.status = "failed"
    source.error = error[:2000]
    source.fetched_at = datetime.now(timezone.utc)
    db.flush()


@register("parse_source")
def run_parse(db: Session, job: Job) -> None:
    """Parse from stored bytes only. Never touches the network."""
    source = db.get(Source, uuid.UUID(job.payload["source_id"]))
    if source is None or not source.blob_ref:
        return

    raw = get_blob().get(source.blob_ref)
    adapter = get_adapter(source.type)

    try:
        parsed = adapter.parse(raw, url=source.url)
    except FetchError as exc:
        _mark_failed(db, source, str(exc))
        return
    except Exception as exc:
        _mark_failed(db, source, f"parse failed: {type(exc).__name__}: {exc}")
        return

    source.title = source.title or parsed["title"]
    source.author = parsed["author"]
    source.published_at = parsed["published_at"]

    # Reparse replaces chunks wholesale so extraction improvements do not leave
    # stale chunks behind.
    db.query(Chunk).filter(Chunk.source_id == source.id).delete(synchronize_session=False)

    chunks = chunk_parsed_source(parsed)
    if not chunks:
        _mark_failed(db, source, "no chunks produced")
        return

    for ordinal, c in enumerate(chunks):
        db.add(
            Chunk(
                id=uuid.uuid4(),
                source_id=source.id,
                ordinal=ordinal,
                text=c["text"],
                start_offset=c.get("start_offset"),
                end_offset=c.get("end_offset"),
                start_ms=c.get("start_ms"),
                end_ms=c.get("end_ms"),
                speaker=c.get("speaker"),
            )
        )

    source.status = "parsed"
    source.error = None
    db.flush()

    queue.enqueue(
        db,
        kind="embed",
        episode_id=job.episode_id,
        user_id=job.user_id,
        payload={"source_id": str(source.id)},
        idempotency_key=f"embed:{source.id}:{source.checksum}",
    )

    episode = db.get(Episode, job.episode_id) if job.episode_id else None
    if episode is not None and episode.guest_entity_id:
        queue.enqueue(
            db,
            kind="extract_claims",
            episode_id=job.episode_id,
            user_id=job.user_id,
            payload={
                "source_id": str(source.id),
                "subject_entity_id": str(episode.guest_entity_id),
            },
            idempotency_key=f"extract:{source.id}:{episode.guest_entity_id}:{source.checksum}",
        )
