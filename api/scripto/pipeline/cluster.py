"""5.6 Cluster claims.

Embed claim text, group by cosine similarity, confirm each group with one LLM
call. A cluster spanning 3 or more DISTINCT SOURCES powers the "already covered"
section -- the most valuable output in the product.

Distinct sources is the point, not distinct claims: the same article quoted
twice is not evidence that someone says a thing repeatedly.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from scripto.config import settings
from scripto.embeddings import get_embedder
from scripto.jobs.limits import provider_slot
from scripto.jobs.registry import register
from scripto.llm import get_llm
from scripto.llm.prompts import CLUSTER_SCHEMA, CLUSTER_SYSTEM, cluster_prompt
from scripto.models import Chunk, Claim, ClaimCluster, Job

MAX_CLUSTER_MEMBERS = 12


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def _greedy_groups(claims: list[Claim], threshold: float) -> list[list[Claim]]:
    """Single-pass greedy clustering against group centroids.

    Good enough at v1 volumes (hundreds of claims per guest) and avoids pulling
    in a clustering dependency. Revisit if a guest ever exceeds a few thousand.
    """
    groups: list[list[Claim]] = []
    centroids: list[list[float]] = []

    for claim in claims:
        if claim.embedding is None:
            continue
        vector = list(claim.embedding)
        best_i, best_score = -1, 0.0
        for i, centroid in enumerate(centroids):
            score = _cosine(vector, centroid)
            if score > best_score:
                best_i, best_score = i, score

        if best_i >= 0 and best_score >= threshold:
            groups[best_i].append(claim)
            members = len(groups[best_i])
            centroids[best_i] = [
                (c * (members - 1) + v) / members
                for c, v in zip(centroids[best_i], vector, strict=True)
            ]
        else:
            groups.append([claim])
            centroids.append(vector)

    return groups


@register("cluster_claims")
def run_cluster(db: Session, job: Job) -> None:
    subject_id = uuid.UUID(job.payload["subject_entity_id"])

    # Clustering rewrites every cluster row for this subject. Two jobs doing
    # that concurrently for the same entity deadlock against each other, so
    # serialise per subject. The lock is transaction scoped and released on
    # commit or rollback; a waiting worker simply runs afterwards and sees the
    # newer claims, which is the behaviour we want anyway.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"cluster:{subject_id}"},
    )

    claims = list(
        db.scalars(
            select(Claim)
            .where(Claim.subject_entity_id == subject_id)
            .order_by(Claim.created_at)
        )
    )
    if not claims:
        return

    # Embed any claim that does not have a vector yet.
    missing = [c for c in claims if c.embedding is None]
    if missing:
        embedder = get_embedder()
        for i in range(0, len(missing), 64):
            batch = missing[i : i + 64]
            with provider_slot("embedding"):
                vectors = embedder.embed([c.text for c in batch], input_type="document")
            for claim, vector in zip(batch, vectors, strict=True):
                claim.embedding = vector
        db.flush()

    # Recluster from scratch; clusters are derived data.
    db.query(ClaimCluster).filter(ClaimCluster.subject_entity_id == subject_id).delete(
        synchronize_session=False
    )
    for claim in claims:
        claim.cluster_id = None
    db.flush()

    llm = get_llm()
    for group in _greedy_groups(claims, settings.cluster_similarity_threshold):
        source_ids = _distinct_source_ids(db, group)

        # Only groups that could reach the "already covered" bar are worth an
        # LLM confirmation call. Singletons still get a cluster row so every
        # claim is addressable, they just skip the call.
        canonical = group[0].text
        if len(source_ids) >= settings.already_covered_min_sources:
            with provider_slot("llm"):
                verdict = llm.complete_json(
                    role="extract",
                    system=CLUSTER_SYSTEM,
                    prompt=cluster_prompt([c.text for c in group[:MAX_CLUSTER_MEMBERS]]),
                    schema=CLUSTER_SCHEMA,
                )
            if not verdict.get("same_claim", False):
                # Rejected: each claim stands alone.
                for claim in group:
                    _singleton(db, claim, subject_id)
                continue
            canonical = verdict.get("canonical_text") or canonical

        dates = [c.claim_date for c in group if c.claim_date]
        cluster = ClaimCluster(
            id=uuid.uuid4(),
            subject_entity_id=subject_id,
            canonical_text=canonical,
            source_count=len(source_ids),
            first_seen=min(dates) if dates else None,
            last_seen=max(dates) if dates else None,
        )
        db.add(cluster)
        db.flush()
        for claim in group:
            claim.cluster_id = cluster.id
        db.flush()


def _distinct_source_ids(db: Session, group: list[Claim]) -> set[uuid.UUID]:
    rows = db.execute(
        select(Chunk.source_id).where(Chunk.id.in_([c.chunk_id for c in group]))
    ).all()
    return {r[0] for r in rows}


def _singleton(db: Session, claim: Claim, subject_id: uuid.UUID) -> None:
    cluster = ClaimCluster(
        id=uuid.uuid4(),
        subject_entity_id=subject_id,
        canonical_text=claim.text,
        source_count=1,
        first_seen=claim.claim_date,
        last_seen=claim.claim_date,
    )
    db.add(cluster)
    db.flush()
    claim.cluster_id = cluster.id


def already_covered(db: Session, subject_id: uuid.UUID) -> list[ClaimCluster]:
    """Clusters spanning enough distinct sources to count as repeated."""
    return list(
        db.scalars(
            select(ClaimCluster)
            .where(
                ClaimCluster.subject_entity_id == subject_id,
                ClaimCluster.source_count >= settings.already_covered_min_sources,
            )
            .order_by(ClaimCluster.source_count.desc())
        )
    )
