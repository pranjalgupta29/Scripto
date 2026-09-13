"""5.8 Generate script: a timed run-of-show.

Inputs: topics, dossier, style preset, interview length, optional voice sample.

The timing plan is computed here, in code, and handed to the model as fixed.
Models are poor at arithmetic -- the same lesson as character offsets in claim
extraction -- and a run-of-show whose blocks do not add up to the booked time is
worse than none. The model writes the words; the code owns the clock.

Voice sample path: extract style descriptors in one call, then pass only those
descriptors into generation. The transcript is never pasted into the script
prompt.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from scripto.blob import get_blob
from scripto.jobs.limits import provider_slot
from scripto.jobs.registry import register
from scripto.llm import get_llm
from scripto.llm.prompts import (
    SCRIPT_SCHEMA,
    SCRIPT_SYSTEM,
    VOICE_SCHEMA,
    VOICE_SYSTEM,
    script_prompt,
    voice_prompt,
)
from scripto.models import (
    Citation,
    Claim,
    DossierItem,
    Entity,
    Episode,
    Job,
    Script,
    ScriptSegment,
    Topic,
)
from scripto.pipeline.cluster import already_covered

STYLE_PRESETS = ("formal", "conversational", "contrarian", "educational")

# Rough minutes one substantive question-and-answer takes, by style. Formal and
# educational answers run longer; conversational and contrarian move faster.
MINUTES_PER_QUESTION = {"formal": 5, "conversational": 4, "contrarian": 4, "educational": 5}

# Opening and closing each take about 7% of the interview, within sane bounds.
EDGE_SHARE = 0.07
EDGE_MIN_MINUTES = 2
EDGE_MAX_MINUTES = 8
MAX_QUESTIONS_PER_TOPIC = 8
MAX_BONUS_TOPICS = 3

AUTO_FILLED_FLAG = (
    "Auto-filled: the generator did not write this block. Regenerate or write your own."
)


def plan_run_of_show(duration_minutes: int, n_topics: int, style: str) -> dict:
    """Split the booked time into opening, topic blocks and closing.

    Topic blocks share the middle equally, with leftover minutes going to the
    earliest topics, so the plan always adds up to exactly the booked time.
    Question counts follow from the minutes and the style's pace.
    """
    n_topics = max(1, n_topics)
    edge = max(EDGE_MIN_MINUTES, min(EDGE_MAX_MINUTES, round(duration_minutes * EDGE_SHARE)))
    middle = max(n_topics, duration_minutes - 2 * edge)
    base, extra = divmod(middle, n_topics)
    topic_minutes = [base + (1 if i < extra else 0) for i in range(n_topics)]

    per_question = MINUTES_PER_QUESTION.get(style, 4)
    questions = [
        max(1, min(MAX_QUESTIONS_PER_TOPIC, round(minutes / per_question)))
        for minutes in topic_minutes
    ]
    return {
        "opening": edge,
        "closing": edge,
        "topic_minutes": topic_minutes,
        "questions": questions,
        "total": 2 * edge + sum(topic_minutes),
    }


def extract_voice_descriptors(blob_ref: str) -> dict | None:
    try:
        transcript = get_blob().get(blob_ref).decode("utf-8", errors="replace")
    except Exception:
        return None
    if not transcript.strip():
        return None

    with provider_slot("llm"):
        payload = get_llm().complete_json(
            role="extract",
            system=VOICE_SYSTEM,
            prompt=voice_prompt(transcript),
            schema=VOICE_SCHEMA,
        )
    return payload.get("descriptors")


def _valid_claims(db: Session, subject_id: uuid.UUID, candidates: list) -> dict[str, Claim]:
    """Real claims about this subject, out of whatever ids the model cited."""
    parsed: list[uuid.UUID] = []
    for value in candidates:
        try:
            parsed.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    if not parsed:
        return {}
    return {
        str(claim.id): claim
        for claim in db.scalars(
            select(Claim).where(Claim.subject_entity_id == subject_id, Claim.id.in_(parsed))
        )
    }


def _text(value) -> str | None:
    value = (value or "").strip() if isinstance(value, str) else ""
    return value or None


def _texts(values) -> list[str]:
    """Clean a list of strings from the model.

    Drops bare UUIDs: on the first real run Gemini put a claim id into
    risk_flags, which would have reached the host as a meaningless flag.
    """
    cleaned: list[str] = []
    for value in values or []:
        if value is None:
            continue
        text = str(value).strip()
        if text and not _is_uuid(text):
            cleaned.append(text)
    return cleaned


def _is_uuid(text: str) -> bool:
    try:
        uuid.UUID(text)
    except ValueError:
        return False
    return True


@register("generate_script")
def run_generate_script(db: Session, job: Job) -> None:
    episode = db.get(Episode, job.episode_id)
    if episode is None or episode.guest_entity_id is None:
        return
    subject = db.get(Entity, episode.guest_entity_id)
    if subject is None:
        return

    script = db.get(Script, uuid.UUID(job.payload["script_id"]))
    if script is None:
        return

    topics = list(
        db.scalars(select(Topic).where(Topic.episode_id == episode.id).order_by(Topic.ordinal))
    )
    if not topics:
        return

    descriptors = None
    if script.voice_sample_ref:
        descriptors = extract_voice_descriptors(script.voice_sample_ref)
        script.voice_descriptors = descriptors
        db.flush()

    # Ground the script in the dossier, carrying claim ids through so every
    # block stays citable.
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

    optimize_order = bool(job.payload.get("optimize_order", True))
    include_bonus = bool(job.payload.get("include_bonus", True))
    plan = plan_run_of_show(script.duration_minutes or 60, len(topics), script.style_preset)

    with provider_slot("llm"):
        payload = get_llm().complete_json(
            role="compose",
            system=SCRIPT_SYSTEM,
            prompt=script_prompt(
                episode_title=episode.title,
                subject=subject.name,
                headline=subject.headline,
                plan=plan,
                topics=[(str(t.id), t.text) for t in topics],
                dossier_lines=dossier_lines,
                style=script.style_preset,
                voice_descriptors=descriptors,
                already_covered=covered,
                optimize_order=optimize_order,
                include_bonus=include_bonus,
            ),
            schema=SCRIPT_SCHEMA,
        )

    opening = payload.get("opening") if isinstance(payload.get("opening"), dict) else {}
    closing = payload.get("closing") if isinstance(payload.get("closing"), dict) else {}
    blocks = [b for b in payload.get("blocks") or [] if isinstance(b, dict)]
    bonus = (
        [b for b in payload.get("bonus") or [] if isinstance(b, dict)] if include_bonus else []
    )

    every_cited = [*(opening.get("claim_ids") or []), *(closing.get("claim_ids") or [])]
    for block in blocks + bonus:
        every_cited.extend(block.get("claim_ids") or [])
    claims_by_id = _valid_claims(db, subject.id, every_cited)

    def cited(ids) -> list[str]:
        return [c for c in (ids or []) if c in claims_by_id]

    # Topic order: the model's when it is allowed to choose, otherwise the
    # host's. Any topic the model skipped still gets its slot.
    topics_by_id = {str(t.id): t for t in topics}
    plan_by_topic = {
        str(t.id): (minutes, count)
        for t, minutes, count in zip(topics, plan["topic_minutes"], plan["questions"])
    }
    block_for: dict[str, dict] = {}
    model_order: list[str] = []
    for block in blocks:
        topic_id = block.get("topic_id")
        if topic_id in topics_by_id and topic_id not in block_for:
            block_for[topic_id] = block
            model_order.append(topic_id)
    host_order = [str(t.id) for t in topics]
    if optimize_order:
        order = model_order + [t for t in host_order if t not in block_for]
    else:
        order = host_order

    # Replace the previous run-of-show, including its citations.
    old_ids = list(
        db.scalars(select(ScriptSegment.id).where(ScriptSegment.script_id == script.id))
    )
    if old_ids:
        db.query(Citation).filter(
            Citation.target_type == "script_segment", Citation.target_id.in_(old_ids)
        ).delete(synchronize_session=False)
        db.query(ScriptSegment).filter(ScriptSegment.script_id == script.id).delete(
            synchronize_session=False
        )
        db.flush()

    rows: list[dict] = [
        {
            "segment_type": "opening",
            "title": "Opening",
            "start_minute": 0,
            "planned_minutes": plan["opening"],
            "transition_in": _text(opening.get("hook")),
            "host_script": _text(opening.get("guest_intro")),
            "question": _text(opening.get("first_question"))
            or f"Welcome, {subject.name}. How did you get to where you are today?",
            "claim_ids": cited(opening.get("claim_ids")),
        }
    ]

    clock = plan["opening"]
    for topic_id in order:
        topic = topics_by_id[topic_id]
        minutes, count = plan_by_topic[topic_id]
        block = block_for.get(topic_id)
        lead = _text(block.get("lead_question")) if block else None

        if lead is None:
            rows.append(
                {
                    "segment_type": "topic",
                    "topic_id": topic.id,
                    "title": topic.text,
                    "start_minute": clock,
                    "planned_minutes": minutes,
                    "question": f"Let's talk about {topic.text}.",
                    "risk_flags": [AUTO_FILLED_FLAG],
                    "claim_ids": [],
                }
            )
        else:
            rows.append(
                {
                    "segment_type": "topic",
                    "topic_id": topic.id,
                    "title": _text(block.get("title")) or topic.text,
                    "start_minute": clock,
                    "planned_minutes": minutes,
                    "transition_in": _text(block.get("transition_in")),
                    "question": lead,
                    # The plan's question count includes the lead question.
                    "deeper_questions": _texts(block.get("deeper_questions"))[: max(0, count - 1)],
                    "followups": _texts(block.get("followups")),
                    "rationale": _text(block.get("rationale")),
                    "expected_direction": _text(block.get("expected_direction")),
                    "risk_flags": _texts(block.get("risk_flags")),
                    "claim_ids": cited(block.get("claim_ids")),
                }
            )
        clock += minutes

    rows.append(
        {
            "segment_type": "closing",
            "title": "Closing",
            "start_minute": clock,
            "planned_minutes": plan["closing"],
            "transition_in": _text(closing.get("transition_in")),
            "question": _text(closing.get("final_question"))
            or "What should listeners take away from this conversation?",
            "host_script": _text(closing.get("wrap_up")),
            "claim_ids": cited(closing.get("claim_ids")),
        }
    )

    for block in bonus[:MAX_BONUS_TOPICS]:
        lead = _text(block.get("lead_question"))
        if lead is None:
            continue
        rows.append(
            {
                "segment_type": "bonus",
                "title": _text(block.get("title")) or "Backup topic",
                "rationale": _text(block.get("why")),
                "question": lead,
                "followups": _texts(block.get("followups")),
                "claim_ids": cited(block.get("claim_ids")),
            }
        )

    for ordinal, row in enumerate(rows):
        claim_ids = row.pop("claim_ids")
        segment = ScriptSegment(
            id=uuid.uuid4(),
            script_id=script.id,
            ordinal=ordinal,
            claim_ids=claim_ids,
            # The closing is connective and makes no claims about the guest;
            # every other block must rest on research or be visibly flagged.
            flagged_unsourced=(not claim_ids) and row["segment_type"] != "closing",
            **row,
        )
        db.add(segment)
        db.flush()

        for claim_id in claim_ids:
            claim = claims_by_id[claim_id]
            db.add(
                Citation(
                    id=uuid.uuid4(),
                    target_type="script_segment",
                    target_id=segment.id,
                    chunk_id=claim.chunk_id,
                    span_start=claim.span_start,
                    span_end=claim.span_end,
                )
            )

    episode.status = "script_ready"
    db.flush()
