"""5.8 Generate script.

Voice sample path: extract style descriptors in one call, then pass those
descriptors into generation. The whole transcript is never pasted into the
script prompt.
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

    # Ground the script in the dossier, carrying claim ids through so segments
    # stay citable.
    items = list(
        db.scalars(
            select(DossierItem)
            .where(DossierItem.episode_id == episode.id)
            .order_by(DossierItem.section, DossierItem.ordinal)
        )
    )
    dossier_lines = [
        f"[{item.claim_ids[0]}] ({item.section}) {item.text}"
        for item in items
        if item.claim_ids
    ]

    covered = [c.canonical_text for c in already_covered(db, subject.id)]

    with provider_slot("llm"):
        payload = get_llm().complete_json(
            role="compose",
            system=SCRIPT_SYSTEM,
            prompt=script_prompt(
                subject.name,
                [(str(t.id), t.text) for t in topics],
                dossier_lines,
                script.style_preset,
                descriptors,
                covered,
            ),
            schema=SCRIPT_SCHEMA,
        )

    valid_topics = {str(t.id) for t in topics}
    valid_claims = {
        str(cid)
        for cid in db.scalars(
            select(Claim.id).where(Claim.subject_entity_id == subject.id)
        )
    }
    claims_by_id = {
        str(c.id): c
        for c in db.scalars(select(Claim).where(Claim.subject_entity_id == subject.id))
    }

    db.query(ScriptSegment).filter(ScriptSegment.script_id == script.id).delete(
        synchronize_session=False
    )
    db.flush()

    for ordinal, raw in enumerate(payload.get("segments", [])):
        question = (raw.get("question") or "").strip()
        if not question:
            continue
        topic_id = raw.get("topic_id")
        if topic_id not in valid_topics:
            topic_id = str(topics[min(ordinal, len(topics) - 1)].id)

        cited = [c for c in raw.get("claim_ids", []) if c in valid_claims]

        segment = ScriptSegment(
            id=uuid.uuid4(),
            script_id=script.id,
            ordinal=ordinal,
            topic_id=uuid.UUID(topic_id),
            question=question,
            rationale=raw.get("rationale"),
            expected_direction=raw.get("expected_direction"),
            followups=raw.get("followups", []),
            risk_flags=raw.get("risk_flags", []),
            claim_ids=cited,
        )
        db.add(segment)
        db.flush()

        for claim_id in cited:
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
