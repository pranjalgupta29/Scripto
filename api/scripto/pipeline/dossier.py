"""5.7 Build dossier.

Fixed sections, each its own retrieval plus generation. Every generated sentence
must return the claim ids it rests on, and a verification pass drops any item
whose claim ids do not resolve to real claims for this subject. No exceptions --
this is what makes "zero unsourced sentences reach the UI" true.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scripto.config import settings
from scripto.jobs.limits import provider_slot
from scripto.jobs.registry import register
from scripto.llm import get_llm
from scripto.llm.prompts import DOSSIER_SCHEMA, DOSSIER_SYSTEM, dossier_prompt
from scripto.models import (
    Chunk,
    Citation,
    Claim,
    ClaimCluster,
    DossierItem,
    Entity,
    Episode,
    EpisodeSource,
    Job,
)

SECTIONS = [
    "career_timeline",
    "recent_news",
    "public_positions",
    "already_covered",
    "unexplored_angles",
]

MAX_CLAIMS_PER_SECTION = 60


def _claims_for_section(
    db: Session, subject_id: uuid.UUID, section: str
) -> list[Claim]:
    stmt = select(Claim).where(Claim.subject_entity_id == subject_id)

    if section == "career_timeline":
        stmt = stmt.where(Claim.kind == "biographical").order_by(
            Claim.claim_date.asc().nullslast()
        )
    elif section == "recent_news":
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        stmt = stmt.where(Claim.claim_date >= cutoff).order_by(Claim.claim_date.desc())
    elif section == "public_positions":
        stmt = stmt.where(Claim.kind.in_(["opinion", "prediction"])).order_by(
            Claim.claim_date.asc().nullslast()
        )
    elif section == "already_covered":
        stmt = (
            stmt.join(ClaimCluster, Claim.cluster_id == ClaimCluster.id)
            .where(ClaimCluster.source_count >= settings.already_covered_min_sources)
            .order_by(ClaimCluster.source_count.desc())
        )
    else:  # unexplored_angles
        stmt = (
            stmt.outerjoin(ClaimCluster, Claim.cluster_id == ClaimCluster.id)
            .where(Claim.kind.in_(["fact", "opinion", "anecdote"]))
            .order_by(ClaimCluster.source_count.asc().nullsfirst())
        )

    return list(db.scalars(stmt.limit(MAX_CLAIMS_PER_SECTION)))


# --------------------------------------------------------------------------
# topic brief: an even share per host topic
# --------------------------------------------------------------------------

_BRIEF_KINDS = ["fact", "opinion", "prediction"]
_RESEARCH_KINDS = ["discover", "fetch_source", "parse_source", "extract_claims"]


def _interleave(groups: list[list], limit: int | None = None) -> list:
    """Take one from each group in turn, so no single group fills the list."""
    out: list = []
    for i in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if i < len(group):
                out.append(group[i])
                if limit is not None and len(out) >= limit:
                    return out
    return out


def _topic_brief_claims(
    db: Session, episode: Episode, subject: Entity
) -> tuple[list[Claim], dict[str, str | None]]:
    """An even share of claims per host topic, spread across each topic's sources.

    The brief used to take the newest 60 topic claims. Almost none carry a date,
    so the first topic researched filled it: one real run offered the writer 44
    claims on focus and none on circadian rhythms or habit extinction.
    """
    rows = db.execute(
        select(Claim, EpisodeSource.topic, Chunk.source_id)
        .join(Chunk, Claim.chunk_id == Chunk.id)
        .join(
            EpisodeSource,
            (EpisodeSource.source_id == Chunk.source_id)
            & (EpisodeSource.episode_id == episode.id),
        )
        .where(
            Claim.subject_entity_id == subject.id,
            Claim.kind.in_(_BRIEF_KINDS),
            EpisodeSource.removed_at.is_(None),
        )
        .order_by(Claim.claim_date.desc().nullslast(), Chunk.ordinal, Claim.span_start)
    ).all()

    current = [t for t in (subject.aliases or []) if t]
    by_topic: dict[str | None, dict[uuid.UUID, list[Claim]]] = {}
    topic_of: dict[str, str | None] = {}
    for claim, topic, source_id in rows:
        if topic is not None and topic not in current:
            continue  # the host has since dropped this topic
        by_topic.setdefault(topic, {}).setdefault(source_id, []).append(claim)
        topic_of[str(claim.id)] = topic

    # Host order first; sources from before topics were recorded share a group.
    order = [t for t in current if t in by_topic] + ([None] if None in by_topic else [])
    per_topic = [_interleave(list(by_topic[t].values())) for t in order]
    picked = _interleave(per_topic, limit=MAX_CLAIMS_PER_SECTION)
    return picked, {str(c.id): topic_of[str(c.id)] for c in picked}


def _topic_gaps(db: Session, episode: Episode, subject: Entity) -> list[str]:
    """Researched topics that yielded nothing usable, so the host sees the gap
    instead of a brief that quietly leaves them out."""
    still_researching = db.scalar(
        select(Job.id)
        .where(
            Job.episode_id == episode.id,
            Job.kind.in_(_RESEARCH_KINDS),
            Job.state.in_(["queued", "running"]),
        )
        .limit(1)
    )
    unlabelled = db.scalar(
        select(EpisodeSource.id)
        .where(
            EpisodeSource.episode_id == episode.id,
            EpisodeSource.subject_entity_id == subject.id,
            EpisodeSource.removed_at.is_(None),
            EpisodeSource.topic.is_(None),
        )
        .limit(1)
    )
    # Mid-research, or sources that predate topic labels: no honest answer yet.
    if still_researching or unlabelled:
        return []

    with_claims = set(
        db.scalars(
            select(EpisodeSource.topic)
            .distinct()
            .join(Chunk, Chunk.source_id == EpisodeSource.source_id)
            .join(Claim, Claim.chunk_id == Chunk.id)
            .where(
                EpisodeSource.episode_id == episode.id,
                EpisodeSource.removed_at.is_(None),
                Claim.subject_entity_id == subject.id,
                Claim.kind.in_(_BRIEF_KINDS),
            )
        )
    )
    researched = set((subject.external_ids or {}).get("researched_topics", []))
    return [t for t in (subject.aliases or []) if t in researched and t not in with_claims]


def compose_section(
    db: Session,
    episode: Episode,
    subject: Entity,
    section: str,
    claims: list[Claim] | None = None,
    topic_of: dict[str, str | None] | None = None,
) -> list[DossierItem]:
    if claims is None:
        claims = _claims_for_section(db, subject.id, section)
    if not claims:
        return []

    valid_ids = {str(c.id) for c in claims}
    by_id = {str(c.id): c for c in claims}

    with provider_slot("llm"):
        payload = get_llm().complete_json(
            role="compose",
            system=DOSSIER_SYSTEM,
            prompt=dossier_prompt(
                section,
                subject.name,
                [
                    (
                        str(c.id),
                        c.text,
                        c.claim_date.date().isoformat() if c.claim_date else None,
                    )
                    for c in claims
                ],
                topic_of=topic_of,
            ),
            schema=DOSSIER_SCHEMA,
        )

    items: list[DossierItem] = []
    for ordinal, raw in enumerate(payload.get("items", [])):
        text = (raw.get("text") or "").strip()
        if not text:
            continue

        # Verification pass: keep only claim ids that are real claims for this
        # subject and were actually offered to the model. A hallucinated id is
        # indistinguishable from no id at all.
        cited = [cid for cid in raw.get("claim_ids", []) if cid in valid_ids]
        if not cited:
            continue  # unsourced sentence, dropped before it can reach the UI

        dated = [by_id[c].claim_date for c in cited if by_id[c].claim_date]
        item = DossierItem(
            id=uuid.uuid4(),
            episode_id=episode.id,
            section=section,
            ordinal=ordinal,
            text=text,
            claim_ids=cited,
            item_date=min(dated) if dated and section == "career_timeline" else (
                max(dated) if dated else None
            ),
            cluster_id=by_id[cited[0]].cluster_id,
            flagged_unsourced=False,
        )
        db.add(item)
        db.flush()

        # Citations point at the chunk span, so the UI can open the source at
        # the exact place the claim came from.
        for claim_id in cited:
            claim = by_id[claim_id]
            db.add(
                Citation(
                    id=uuid.uuid4(),
                    target_type="dossier_item",
                    target_id=item.id,
                    chunk_id=claim.chunk_id,
                    span_start=claim.span_start,
                    span_end=claim.span_end,
                )
            )
        items.append(item)

    db.flush()
    return items


@register("build_dossier")
def run_build_dossier(db: Session, job: Job) -> None:
    episode = db.get(Episode, job.episode_id)
    if episode is None or episode.guest_entity_id is None:
        return
    subject = db.get(Entity, episode.guest_entity_id)
    if subject is None:
        return

    # Dossiers are derived; rebuild wholesale.
    old = list(db.scalars(select(DossierItem.id).where(DossierItem.episode_id == episode.id)))
    if old:
        db.query(Citation).filter(
            Citation.target_type == "dossier_item", Citation.target_id.in_(old)
        ).delete(synchronize_session=False)
        db.query(DossierItem).filter(DossierItem.episode_id == episode.id).delete(
            synchronize_session=False
        )
        db.flush()

    for section in SECTIONS:
        compose_section(db, episode, subject, section)

    # Every episode whose topics were researched gets a topic brief, through the
    # very same composer. (The spec limited this to thin and sparse guests.)
    gaps: list[str] = []
    if episode.topic_entity_id:
        topic_entity = db.get(Entity, episode.topic_entity_id)
        if topic_entity is not None:
            claims, topic_of = _topic_brief_claims(db, episode, topic_entity)
            compose_section(
                db, episode, topic_entity, "topic_brief", claims=claims, topic_of=topic_of
            )
            gaps = _topic_gaps(db, episode, topic_entity)
    episode.coverage_detail = {**(episode.coverage_detail or {}), "topic_gaps": gaps}

    produced = db.scalar(
        select(func.count(DossierItem.id)).where(DossierItem.episode_id == episode.id)
    )

    # Never report success on an empty page. If claims exist but composition
    # produced nothing, that is a real failure and the job should retry rather
    # than leave the user staring at a dossier that says nothing.
    claim_total = db.scalar(
        select(func.count(Claim.id)).where(Claim.subject_entity_id == subject.id)
    )
    if not produced and claim_total:
        raise RuntimeError(
            f"composed 0 dossier items from {claim_total} claims for episode {episode.id}"
        )

    # A rebuild must not move an episode that already has a script back a step.
    if episode.status != "script_ready":
        episode.status = "dossier_ready"
    db.flush()
