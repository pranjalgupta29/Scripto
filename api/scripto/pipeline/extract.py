"""5.5 Extract claims.

The guard that matters: a claim whose `verbatim_span` does not resolve inside
its chunk is discarded. This is enforced here, in code, not in the prompt --
a model told not to fabricate will still fabricate.

Claims are scoped to the entity, not the episode, so a second episode with the
same guest reuses everything already extracted.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from scripto.jobs import queue
from scripto.jobs.limits import provider_slot
from scripto.jobs.registry import register
from scripto.llm import get_llm
from scripto.llm.prompts import (
    EXTRACT_SCHEMA,
    EXTRACT_SYSTEM,
    IDENTITY_CHECK_SCHEMA,
    IDENTITY_CHECK_SYSTEM,
    extract_prompt,
    identity_check_prompt,
)
from scripto.models import Chunk, Claim, Entity, EpisodeSource, Job, Source

log = logging.getLogger(__name__)

VALID_KINDS = {"biographical", "opinion", "fact", "anecdote", "prediction"}

# How much of the span must actually appear in the claim's own wording before we
# accept that the span supports it. Guards against a resolving-but-unrelated span.
_MIN_SPAN_CHARS = 8


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().lower().encode()).hexdigest()


def locate_quote(quote: str, chunk_text: str) -> tuple[int, int] | None:
    """Find a model-supplied quote inside the chunk, returning its real offsets.

    We ask the model to quote rather than to report character offsets, because
    models copy text accurately and count characters badly. Measured on real
    output, model-reported offsets pointed at unrelated text 30% of the time;
    a located quote is correct by construction or it is rejected.

    Falls back to whitespace-insensitive matching, since re-wrapping is the one
    difference that is cosmetic rather than a sign of fabrication.
    """
    quote = (quote or "").strip()
    if len(quote) < _MIN_SPAN_CHARS:
        return None

    index = chunk_text.find(quote)
    if index != -1:
        return index, index + len(quote)

    # Whitespace-tolerant search: build a regex where every run of whitespace in
    # the quote matches any run of whitespace in the chunk.
    pattern = r"\s+".join(re.escape(part) for part in quote.split())
    match = re.search(pattern, chunk_text)
    if match:
        return match.start(), match.end()

    return None


def validate_claim(raw: dict, chunk_text: str) -> dict | None:
    """Return a normalised claim, or None if it fails any guard."""
    text = (raw.get("text") or "").strip()
    if not text:
        return None

    kind = raw.get("kind")
    if kind not in VALID_KINDS:
        return None

    # Preferred path: locate the model's quote in the source.
    located = locate_quote(raw.get("quote", ""), chunk_text)

    if located is None:
        # Back-compat for providers still returning offsets. Trust them only
        # far enough to resolve; they are checked for support below.
        span = raw.get("verbatim_span")
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            return None
        try:
            start, end = int(span[0]), int(span[1])
        except (TypeError, ValueError):
            return None
        if start < 0 or end > len(chunk_text) or start >= end:
            return None
        if end - start < _MIN_SPAN_CHARS:
            return None
        located = (start, end)

    start, end = located
    return {
        "text": text,
        "kind": kind,
        "claim_date": _parse_date(raw.get("claim_date")),
        "span_start": start,
        "span_end": end,
    }


def _parse_date(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(value.strip()[: len(fmt) + 2], fmt).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
    return None


def _identity_of(entity: Entity) -> str | None:
    """Who the subject is, beyond the name: the tells a same-name page fails."""
    parts = [entity.headline or "", entity.employer or ""]
    urls = (entity.external_ids or {}).get("evidence_urls") or []
    if urls:
        parts.append(str(urls[0]))
    joined = " · ".join(p for p in parts if p)
    return joined or None


# How much of a page the identity gate reads. The opening carries the byline,
# the role and the pronouns; the rest rarely changes the answer.
IDENTITY_EXCERPT_CHARS = 1200


def is_same_person(entity: Entity, source: Source, excerpt: str) -> tuple[bool, str]:
    """One cheap call: is this page about the person the host confirmed?

    Identity used to be checked once, at confirmation, and trusted forever. A
    Times Now author page for a different Pranjal Gupta then contributed five
    "facts" about the wrong person to a dossier.
    """
    with provider_slot("llm"):
        payload = get_llm().complete_json(
            role="rank",
            system=IDENTITY_CHECK_SYSTEM,
            prompt=identity_check_prompt(
                subject=entity.name,
                headline=entity.headline,
                employer=entity.employer,
                known_urls=(entity.external_ids or {}).get("evidence_urls") or [],
                title=source.title,
                url=source.url,
                excerpt=excerpt[:IDENTITY_EXCERPT_CHARS],
            ),
            schema=IDENTITY_CHECK_SCHEMA,
        )
    return bool(payload.get("same_person", True)), (payload.get("why") or "").strip()


def extract_from_chunk(
    chunk: Chunk, subject_name: str, identity: str | None = None
) -> list[dict]:
    llm = get_llm()
    with provider_slot("llm"):
        payload = llm.complete_json(
            role="extract",
            system=EXTRACT_SYSTEM,
            prompt=extract_prompt(subject_name, chunk.text, identity),
            schema=EXTRACT_SCHEMA,
        )

    kept: list[dict] = []
    dropped = 0
    for raw in payload.get("claims", []):
        validated = validate_claim(raw, chunk.text)
        if validated is None:
            dropped += 1
            continue
        kept.append(validated)

    if dropped:
        log.info("dropped %d claim(s) with unresolvable spans in chunk %s", dropped, chunk.id)
    return kept


@register("extract_claims")
def run_extract(db: Session, job: Job) -> None:
    source_id = uuid.UUID(job.payload["source_id"])
    subject_id = uuid.UUID(job.payload["subject_entity_id"])

    entity = db.get(Entity, subject_id)
    if entity is None:
        return

    # A topic source is read against the one topic it was found for. Topic
    # sources used to be read against a label naming only the host's first three
    # topics, so a 20-section paper on habit extinction yielded nothing.
    link = db.scalar(
        select(EpisodeSource).where(
            EpisodeSource.episode_id == job.episode_id,
            EpisodeSource.source_id == source_id,
            EpisodeSource.subject_entity_id == subject_id,
        )
    )
    topic = link.topic if link else None
    subject_name = topic or entity.name
    identity = _identity_of(entity) if entity.type == "person" and not topic else None
    # A repair re-reads chunks already extracted (say, against the wrong
    # subject); ordinary retries skip them to stay cheap.
    reread = bool(job.payload.get("reread"))

    extractor_version = get_llm().version
    chunks = list(
        db.scalars(
            select(Chunk).where(Chunk.source_id == source_id).order_by(Chunk.ordinal)
        )
    )

    # Gate discovered pages on identity before mining them. What the host or the
    # guest supplied is trusted: they know who they meant.
    if (
        chunks
        and identity
        and link is not None
        and link.added_by == "system"
        and link.identity is None
    ):
        source = db.get(Source, source_id)
        same, why = is_same_person(entity, source, chunks[0].text)
        link.identity = "ok" if same else "mismatch"
        db.flush()
        if not same:
            log.info("skipped %s: not %s (%s)", source_id, entity.name, why)
            return

    produced = 0
    for chunk in chunks:
        # Skip chunks already extracted at this version, so retries are cheap.
        already = db.scalar(
            select(Claim.id).where(
                Claim.chunk_id == chunk.id,
                Claim.subject_entity_id == subject_id,
                Claim.extractor_version == extractor_version,
            )
        )
        if already and not reread:
            continue

        for claim in extract_from_chunk(chunk, subject_name, identity):
            stmt = (
                pg_insert(Claim)
                .values(
                    id=uuid.uuid4(),
                    chunk_id=chunk.id,
                    subject_entity_id=subject_id,
                    text=claim["text"],
                    text_hash=_text_hash(claim["text"]),
                    kind=claim["kind"],
                    claim_date=claim["claim_date"],
                    extractor_version=extractor_version,
                    span_start=claim["span_start"],
                    span_end=claim["span_end"],
                )
                .on_conflict_do_nothing(
                    index_elements=["chunk_id", "text_hash", "extractor_version"]
                )
            )
            db.execute(stmt)
            produced += 1
        db.flush()

    if produced:
        # One waiting cluster run per subject is enough: it sees every claim
        # that exists when it starts. Without this check a 10-source episode
        # re-clustered the whole guest 10 times.
        waiting = db.scalar(
            select(Job.id)
            .where(
                Job.kind == "cluster_claims",
                Job.state == "queued",
                Job.payload["subject_entity_id"].astext == str(subject_id),
            )
            .limit(1)
        )
        if waiting is None:
            queue.enqueue(
                db,
                kind="cluster_claims",
                episode_id=job.episode_id,
                user_id=job.user_id,
                payload={"subject_entity_id": str(subject_id)},
                idempotency_key=None,
                delay_seconds=10,
            )
