"""Prep questionnaire.

The guest is the one source no search can reach. This drafts what to ask them,
grounded in whatever research already exists and shaped by the kind of show, so
the host edits a draft rather than facing a blank page. Nothing here is sent
anywhere: the host reviews and saves the questions, and only then can a link
exist (`routes/prep.py`).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from scripto.jobs.limits import provider_slot
from scripto.llm import get_llm
from scripto.llm.prompts import (
    PREP_QUESTIONS_SCHEMA,
    PREP_QUESTIONS_SYSTEM,
    PREP_STYLE_BRIEFS,
    prep_questions_prompt,
)
from scripto.models import Chunk, DossierItem, Entity, Episode, EpisodeSource, Source, Topic
from scripto.pipeline.suggest import _valid_claim_ids

MAX_QUESTIONS = 6
DEFAULT_STYLE = "conversational"

# Enough of the guest's own words to steer a run-of-show without crowding out
# the research in the prompt.
GUEST_PREP_CHARS = 3000


def guest_prep_text(db: Session, episode: Episode) -> str | None:
    """What the guest said they want, in their own words, or None if nothing.

    Their answers are already a source, cited like any other. This hands the
    same material to the script writer as *preference* -- what to spend time on,
    what to avoid -- which is not something a claim can express.
    """
    rows = (
        db.execute(
            select(Chunk.text)
            .join(Source, Source.id == Chunk.source_id)
            .join(EpisodeSource, EpisodeSource.source_id == Source.id)
            .where(
                EpisodeSource.episode_id == episode.id,
                EpisodeSource.added_by == "guest",
                EpisodeSource.removed_at.is_(None),
            )
            .order_by(Source.created_at, Chunk.ordinal)
        )
        .scalars()
        .all()
    )
    text = "\n\n".join(t.strip() for t in rows if t and t.strip())
    return text[:GUEST_PREP_CHARS] or None


def suggest_prep_questions(db: Session, episode: Episode, style: str) -> list[dict]:
    """One compose call. Returns [{text, why, basis, claim_ids}], not persisted.

    Runs inline like topic suggestions, because the host is waiting on it.
    """
    subject = db.get(Entity, episode.guest_entity_id) if episode.guest_entity_id else None
    if subject is None:
        return []

    if style not in PREP_STYLE_BRIEFS:
        style = DEFAULT_STYLE

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

    with provider_slot("llm"):
        payload = get_llm().complete_json(
            role="compose",
            system=PREP_QUESTIONS_SYSTEM,
            prompt=prep_questions_prompt(
                episode_title=episode.title,
                subject=subject.name,
                headline=subject.headline,
                style=style,
                existing_topics=existing,
                dossier_lines=dossier_lines,
            ),
            schema=PREP_QUESTIONS_SCHEMA,
        )

    raw_questions = [q for q in payload.get("questions") or [] if isinstance(q, dict)]
    valid = _valid_claim_ids(
        db, subject.id, [c for q in raw_questions for c in q.get("claim_ids") or []]
    )

    # Deduplication in code, not trusted to the prompt.
    seen: set[str] = set()
    questions: list[dict] = []
    for raw in raw_questions:
        text = (raw.get("text") or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        cited = [c for c in raw.get("claim_ids") or [] if c in valid]
        questions.append(
            {
                "text": text,
                "why": (raw.get("why") or "").strip() or None,
                "basis": "research" if cited else "format",
                "claim_ids": cited,
            }
        )
        if len(questions) >= MAX_QUESTIONS:
            break
    return questions
