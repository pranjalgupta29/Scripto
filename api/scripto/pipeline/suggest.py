"""Topic suggestions.

This reverses a v1 non-goal: the spec kept topic ideation out of scope. The host
asked for it, so suggestions exist, but they follow the product's rule. A
suggestion either rests on researched claims, shown with their evidence, or is
labelled plainly as coming from the episode title alone.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from scripto.jobs.limits import provider_slot
from scripto.llm import get_llm
from scripto.llm.prompts import SUGGEST_SCHEMA, SUGGEST_SYSTEM, suggest_prompt
from scripto.models import Claim, DossierItem, Entity, Episode, Topic
from scripto.pipeline.cluster import already_covered

MAX_SUGGESTIONS = 10


def _valid_claim_ids(db: Session, subject_id: uuid.UUID, candidates: list) -> set[str]:
    parsed: list[uuid.UUID] = []
    for value in candidates:
        try:
            parsed.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    if not parsed:
        return set()
    return {
        str(claim_id)
        for claim_id in db.scalars(
            select(Claim.id).where(Claim.subject_entity_id == subject_id, Claim.id.in_(parsed))
        )
    }


def suggest_topics(db: Session, episode: Episode) -> list[dict]:
    """One compose call. Returns [{text, why, basis, claim_ids}], not persisted.

    Runs inline like identify, because the host is waiting to pick topics.
    """
    subject = db.get(Entity, episode.guest_entity_id) if episode.guest_entity_id else None
    if subject is None:
        return []

    existing = [
        t.text
        for t in db.scalars(
            select(Topic).where(Topic.episode_id == episode.id).order_by(Topic.ordinal)
        )
    ]
    items = list(
        db.scalars(
            select(DossierItem)
            .where(DossierItem.episode_id == episode.id)
            .order_by(DossierItem.section, DossierItem.ordinal)
        )
    )
    dossier_lines = [
        f"[{item.claim_ids[0]}] ({item.section}) {item.text}" for item in items if item.claim_ids
    ]
    covered = [c.canonical_text for c in already_covered(db, subject.id)]

    with provider_slot("llm"):
        payload = get_llm().complete_json(
            role="compose",
            system=SUGGEST_SYSTEM,
            prompt=suggest_prompt(
                episode_title=episode.title,
                subject=subject.name,
                headline=subject.headline,
                existing_topics=existing,
                dossier_lines=dossier_lines,
                already_covered=covered,
            ),
            schema=SUGGEST_SCHEMA,
        )

    raw_suggestions = [s for s in payload.get("suggestions") or [] if isinstance(s, dict)]
    valid = _valid_claim_ids(
        db, subject.id, [c for s in raw_suggestions for c in s.get("claim_ids") or []]
    )

    # Enforced in code rather than trusted to the prompt: no repeats of the
    # host's own topics, no duplicates among the suggestions.
    seen = {t.strip().lower() for t in existing}
    suggestions: list[dict] = []
    for raw in raw_suggestions:
        text = (raw.get("text") or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        cited = [c for c in raw.get("claim_ids") or [] if c in valid]
        suggestions.append(
            {
                "text": text,
                "why": (raw.get("why") or "").strip() or None,
                "basis": "research" if cited else "title",
                "claim_ids": cited,
            }
        )
        if len(suggestions) >= MAX_SUGGESTIONS:
            break
    return suggestions
