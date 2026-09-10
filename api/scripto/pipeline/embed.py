"""5.4 Embedding. Batched, and idempotent on re-run."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from scripto.embeddings import get_embedder
from scripto.jobs.limits import provider_slot
from scripto.jobs.registry import register
from scripto.models import Chunk, Job

BATCH = 64


@register("embed")
def run_embed(db: Session, job: Job) -> None:
    source_id = uuid.UUID(job.payload["source_id"])
    chunks = list(
        db.scalars(
            select(Chunk)
            .where(Chunk.source_id == source_id, Chunk.embedding.is_(None))
            .order_by(Chunk.ordinal)
        )
    )
    if not chunks:
        return

    embedder = get_embedder()
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i : i + BATCH]
        with provider_slot("embedding"):
            vectors = embedder.embed([c.text for c in batch], input_type="document")
        for chunk, vector in zip(batch, vectors, strict=True):
            chunk.embedding = vector
        db.flush()
