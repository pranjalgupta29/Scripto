"""Episode routes. Long running work returns a job id; the client polls GET /episodes/{id}."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scripto.auth import current_user
from scripto.blob import checksum, get_blob
from scripto.config import settings
from scripto.db import get_db
from scripto.jobs import queue
from scripto.jobs.limits import BudgetExceeded, SlotUnavailable
from scripto.llm import LLMError
from scripto.models import (
    Chunk,
    Citation,
    Claim,
    DossierItem,
    Episode,
    EpisodeSource,
    Script,
    ScriptSegment,
    Source,
    Topic,
    UsageCounter,
    User,
)
from scripto.pipeline.identify import confirm_guest, identify_candidates
from scripto.schemas import (
    AddSourceRequest,
    Candidate,
    CandidatesResponse,
    CitationOut,
    ConfirmGuestRequest,
    CreateEpisodeRequest,
    CreateScriptRequest,
    DossierItemOut,
    DossierResponse,
    DossierSection,
    EpisodeOut,
    JobProgress,
    PatchSegmentRequest,
    ScriptResponse,
    SegmentOut,
    SourceOut,
    SuggestTopicsResponse,
    TopicOut,
    TopicsRequest,
    TopicSuggestion,
)

router = APIRouter(tags=["episodes"])


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _owned_episode(episode_id: uuid.UUID, db: Session, user: User) -> Episode:
    episode = db.get(Episode, episode_id)
    if episode is None or episode.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "episode not found")
    return episode


def _check_quota(db: Session, user: User) -> None:
    today = date.today().isoformat()
    counter = db.scalar(
        select(UsageCounter).where(
            UsageCounter.user_id == user.id,
            UsageCounter.day == today,
            UsageCounter.kind == "episodes",
        )
    )
    if counter is None:
        counter = UsageCounter(id=uuid.uuid4(), user_id=user.id, day=today, kind="episodes", count=0)
        db.add(counter)
        db.flush()
    if counter.count >= settings.max_episodes_per_user_per_day:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"daily episode limit reached ({settings.max_episodes_per_user_per_day})",
        )
    counter.count += 1


def _sources_for(db: Session, episode_id: uuid.UUID) -> list[SourceOut]:
    topic_id = db.scalar(select(Episode.topic_entity_id).where(Episode.id == episode_id))
    rows = db.execute(
        select(Source, EpisodeSource.added_by, EpisodeSource.subject_entity_id)
        .join(EpisodeSource, EpisodeSource.source_id == Source.id)
        .where(EpisodeSource.episode_id == episode_id, EpisodeSource.removed_at.is_(None))
        .order_by(Source.created_at)
    ).all()
    return [
        SourceOut(
            id=s.id,
            type=s.type,
            url=s.url,
            title=s.title,
            author=s.author,
            published_at=s.published_at,
            status=s.status,
            error=s.error,
            added_by=added_by,
            subject="topic" if topic_id and subject_id == topic_id else "guest",
        )
        for s, added_by, subject_id in rows
    ]


def _citations_for(db: Session, target_type: str, target_ids: list[uuid.UUID]) -> dict:
    """Hydrate citations into openable quotes.

    Every line in the dossier and the script must link to a source the user can
    open, so a citation carries the quoted span itself, not just an id.
    """
    if not target_ids:
        return {}

    rows = db.execute(
        select(Citation, Chunk, Source)
        .join(Chunk, Citation.chunk_id == Chunk.id)
        .join(Source, Chunk.source_id == Source.id)
        .where(Citation.target_type == target_type, Citation.target_id.in_(target_ids))
    ).all()

    out: dict[uuid.UUID, list[CitationOut]] = {}
    for citation, chunk, source in rows:
        start = citation.span_start or 0
        end = citation.span_end or min(len(chunk.text), 300)
        out.setdefault(citation.target_id, []).append(
            CitationOut(
                chunk_id=chunk.id,
                source_id=source.id,
                source_title=source.title,
                source_url=source.url,
                quote=chunk.text[start:end],
                start_ms=chunk.start_ms,
                end_ms=chunk.end_ms,
            )
        )
    return out


# --------------------------------------------------------------------------
# episodes
# --------------------------------------------------------------------------


@router.post("/episodes", response_model=EpisodeOut, status_code=status.HTTP_201_CREATED)
def create_episode(
    body: CreateEpisodeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> EpisodeOut:
    _check_quota(db, user)
    episode = Episode(
        id=uuid.uuid4(),
        user_id=user.id,
        title=body.title,
        guest_name=body.guest_name,
        disambiguator=body.disambiguator,
        status="identifying",
    )
    db.add(episode)
    db.commit()
    return _episode_out(db, episode)


@router.post("/episodes/{episode_id}/identify", response_model=CandidatesResponse)
def identify(
    episode_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> CandidatesResponse:
    """Runs inline: the user is waiting on this and cannot proceed without it."""
    episode = _owned_episode(episode_id, db, user)

    candidates = identify_candidates(episode.guest_name, episode.disambiguator)
    if not candidates:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "no candidate identities found; try a more specific disambiguator",
        )

    episode.coverage_detail = {**(episode.coverage_detail or {}), "candidates": candidates}
    db.commit()
    return CandidatesResponse(candidates=[Candidate(**c) for c in candidates])


@router.post("/episodes/{episode_id}/confirm-guest", response_model=EpisodeOut)
def confirm(
    episode_id: uuid.UUID,
    body: ConfirmGuestRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> EpisodeOut:
    episode = _owned_episode(episode_id, db, user)
    candidates = (episode.coverage_detail or {}).get("candidates", [])
    if body.candidate_index >= len(candidates):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "candidate_index out of range")

    confirm_guest(db, episode, candidates[body.candidate_index])
    queue.enqueue(
        db,
        kind="discover",
        episode_id=episode.id,
        user_id=user.id,
        payload={},
        idempotency_key=f"discover:{episode.id}",
    )
    db.commit()
    # The guest relationship was loaded as None before confirmation.
    db.refresh(episode)
    return _episode_out(db, episode)


@router.get("/episodes/{episode_id}", response_model=EpisodeOut)
def get_episode(
    episode_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> EpisodeOut:
    return _episode_out(db, _owned_episode(episode_id, db, user))


@router.get("/episodes", response_model=list[EpisodeOut])
def list_episodes(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> list[EpisodeOut]:
    episodes = db.scalars(
        select(Episode).where(Episode.user_id == user.id).order_by(Episode.created_at.desc())
    )
    return [_episode_out(db, e, include_sources=False) for e in episodes]


def _episode_out(db: Session, episode: Episode, *, include_sources: bool = True) -> EpisodeOut:
    detail = episode.coverage_detail or {}
    return EpisodeOut(
        id=episode.id,
        title=episode.title,
        guest_name=episode.guest_name,
        disambiguator=episode.disambiguator,
        status=episode.status,
        coverage_mode=episode.coverage_mode,
        coverage_detail={k: v for k, v in detail.items() if k != "candidates"} or None,
        created_at=episode.created_at,
        guest=episode.guest,
        sources=_sources_for(db, episode.id) if include_sources else [],
        progress=JobProgress(**queue.episode_progress(db, episode.id)),
        candidates=[Candidate(**c) for c in detail.get("candidates", [])],
    )


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------


@router.post("/episodes/{episode_id}/sources", response_model=SourceOut, status_code=201)
def add_source(
    episode_id: uuid.UUID,
    body: AddSourceRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> SourceOut:
    from scripto.adapters import canonicalize_url, classify_url
    from scripto.pipeline.discover import attach_source

    episode = _owned_episode(episode_id, db, user)

    # Only sources the host added count here. Discovered sources have their own
    # allowance and must never stop the host pasting a bio or notes -- the main
    # remedy for a thin guest.
    current = db.scalar(
        select(func.count(EpisodeSource.id)).where(
            EpisodeSource.episode_id == episode.id,
            EpisodeSource.removed_at.is_(None),
            EpisodeSource.added_by == "user",
        )
    )
    if current and current >= settings.max_sources_per_episode:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"you have added the maximum of {settings.max_sources_per_episode} sources "
            "to this episode",
        )

    if body.text:
        # Pasted text is stored as a blob immediately and needs no fetch.
        raw = body.text.encode()
        digest = checksum(raw)
        existing = db.scalar(select(Source).where(Source.checksum == digest))
        if existing is None:
            existing = Source(
                id=uuid.uuid4(),
                type="user_pasted",
                url=None,
                canonical_url=f"pasted:{digest}",
                title=body.title,
                blob_ref=get_blob().put(digest, raw),
                checksum=digest,
                fetched_at=datetime.now(timezone.utc),
                status="fetched",
            )
            db.add(existing)
            db.flush()
        db.add(
            EpisodeSource(
                episode_id=episode.id,
                source_id=existing.id,
                added_by="user",
                subject_entity_id=episode.guest_entity_id,
            )
        )
        db.flush()
        queue.enqueue(
            db,
            kind="parse_source",
            episode_id=episode.id,
            user_id=user.id,
            payload={"source_id": str(existing.id)},
            idempotency_key=f"parse:{existing.id}:{digest}",
        )
        source, added_by = existing, "user"
    elif body.url:
        source = attach_source(
            db,
            episode=episode,
            url=body.url,
            source_type=classify_url(canonicalize_url(body.url)),
            added_by="user",
            title=body.title,
            subject_entity_id=episode.guest_entity_id,
        )
        if source is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid url")
        if source.status == "pending":
            queue.enqueue(
                db,
                kind="fetch_source",
                episode_id=episode.id,
                user_id=user.id,
                payload={"source_id": str(source.id)},
                idempotency_key=f"fetch:{source.id}",
            )
        added_by = "user"
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "provide either url or text")

    # New material can change the coverage verdict, so re-score.
    queue.enqueue(
        db,
        kind="coverage_check",
        episode_id=episode.id,
        user_id=user.id,
        payload={},
        delay_seconds=10,
    )
    db.commit()

    return SourceOut(
        id=source.id,
        type=source.type,
        url=source.url,
        title=source.title,
        author=source.author,
        published_at=source.published_at,
        status=source.status,
        error=source.error,
        added_by=added_by,
    )


@router.delete("/episodes/{episode_id}/sources/{source_id}", status_code=204)
def remove_source(
    episode_id: uuid.UUID,
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Response:
    """Removal is per episode; it never deletes the global source."""
    episode = _owned_episode(episode_id, db, user)
    link = db.scalar(
        select(EpisodeSource).where(
            EpisodeSource.episode_id == episode.id, EpisodeSource.source_id == source_id
        )
    )
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not attached to this episode")
    link.removed_at = datetime.now(timezone.utc)
    db.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------
# dossier and topics
# --------------------------------------------------------------------------


@router.get("/episodes/{episode_id}/dossier", response_model=DossierResponse)
def get_dossier(
    episode_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DossierResponse:
    episode = _owned_episode(episode_id, db, user)
    items = list(
        db.scalars(
            select(DossierItem)
            .where(DossierItem.episode_id == episode.id)
            .order_by(DossierItem.section, DossierItem.ordinal)
        )
    )
    citations = _citations_for(db, "dossier_item", [i.id for i in items])

    grouped: dict[str, list[DossierItemOut]] = {}
    for item in items:
        grouped.setdefault(item.section, []).append(
            DossierItemOut(
                id=item.id,
                section=item.section,
                ordinal=item.ordinal,
                text=item.text,
                item_date=item.item_date,
                citations=citations.get(item.id, []),
            )
        )

    return DossierResponse(
        episode_id=episode.id,
        coverage_mode=episode.coverage_mode,
        coverage_detail=episode.coverage_detail,
        sections=[DossierSection(section=k, items=v) for k, v in grouped.items()],
    )


@router.post("/episodes/{episode_id}/topics", response_model=list[TopicOut])
def set_topics(
    episode_id: uuid.UUID,
    body: TopicsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> list[TopicOut]:
    episode = _owned_episode(episode_id, db, user)
    db.query(Topic).filter(Topic.episode_id == episode.id).delete(synchronize_session=False)

    topics = [
        Topic(id=uuid.uuid4(), episode_id=episode.id, ordinal=i, text=t.strip())
        for i, t in enumerate(body.topics)
        if t.strip()
    ]
    db.add_all(topics)
    db.flush()

    # Topics arriving after a thin verdict are what unlock topic research.
    if episode.coverage_mode in ("thin", "sparse") and episode.topic_entity_id is None:
        queue.enqueue(
            db,
            kind="coverage_check",
            episode_id=episode.id,
            user_id=user.id,
            payload={},
        )
    db.commit()
    return [TopicOut.model_validate(t) for t in topics]


@router.get("/episodes/{episode_id}/topics", response_model=list[TopicOut])
def get_topics(
    episode_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> list[TopicOut]:
    episode = _owned_episode(episode_id, db, user)
    topics = db.scalars(
        select(Topic).where(Topic.episode_id == episode.id).order_by(Topic.ordinal)
    )
    return [TopicOut.model_validate(t) for t in topics]


@router.post("/episodes/{episode_id}/topics/suggest", response_model=SuggestTopicsResponse)
def suggest_episode_topics(
    episode_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> SuggestTopicsResponse:
    """Suggest topics from the episode title and the research. Runs inline."""
    from scripto.pipeline.suggest import suggest_topics

    episode = _owned_episode(episode_id, db, user)
    if episode.guest_entity_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "confirm the guest before asking for topic suggestions"
        )

    try:
        suggestions = suggest_topics(db, episode)
    except BudgetExceeded as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except SlotUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"suggestion failed: {exc}") from exc
    db.commit()

    evidence = _claim_citations(db, [c for s in suggestions for c in s["claim_ids"]])
    return SuggestTopicsResponse(
        episode_id=episode.id,
        coverage_mode=episode.coverage_mode,
        suggestions=[
            TopicSuggestion(
                **s, citations=[evidence[c] for c in s["claim_ids"] if c in evidence]
            )
            for s in suggestions
        ],
    )


def _claim_citations(db: Session, claim_ids: list[str]) -> dict[str, CitationOut]:
    """Quoted evidence for claims that are not yet attached to any stored item."""
    parsed: list[uuid.UUID] = []
    for value in claim_ids:
        try:
            parsed.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    if not parsed:
        return {}
    rows = db.execute(
        select(Claim, Chunk, Source)
        .join(Chunk, Claim.chunk_id == Chunk.id)
        .join(Source, Chunk.source_id == Source.id)
        .where(Claim.id.in_(parsed))
    ).all()
    return {
        str(claim.id): CitationOut(
            chunk_id=chunk.id,
            source_id=source.id,
            source_title=source.title,
            source_url=source.url,
            quote=chunk.text[claim.span_start : claim.span_end],
            start_ms=chunk.start_ms,
            end_ms=chunk.end_ms,
        )
        for claim, chunk, source in rows
    }


# --------------------------------------------------------------------------
# script
# --------------------------------------------------------------------------


@router.post("/episodes/{episode_id}/script", response_model=ScriptResponse, status_code=202)
def create_script(
    episode_id: uuid.UUID,
    body: CreateScriptRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> ScriptResponse:
    from scripto.llm import get_llm

    episode = _owned_episode(episode_id, db, user)
    topics = db.scalar(select(func.count(Topic.id)).where(Topic.episode_id == episode.id))
    if not topics:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "set topics before generating a script")

    voice_ref = None
    if body.voice_sample:
        raw = body.voice_sample.encode()
        voice_ref = get_blob().put(f"voice-{checksum(raw)}", raw)

    # A regenerate revises the latest version; that version is kept.
    previous = db.scalar(
        select(Script)
        .where(Script.episode_id == episode.id)
        .order_by(Script.created_at.desc())
        .limit(1)
    )

    script = Script(
        id=uuid.uuid4(),
        episode_id=episode.id,
        style_preset=body.style_preset,
        voice_sample_ref=voice_ref,
        model_version=get_llm().version,
        duration_minutes=body.duration_minutes,
        parent_script_id=previous.id if previous else None,
        feedback=(body.feedback or "").strip() or None,
    )
    db.add(script)
    db.flush()

    queue.enqueue(
        db,
        kind="generate_script",
        episode_id=episode.id,
        user_id=user.id,
        payload={
            "script_id": str(script.id),
            "optimize_order": body.optimize_order,
            "include_bonus": body.include_bonus,
        },
        idempotency_key=f"script:{script.id}",
    )
    db.commit()
    return _script_out(db, script)


@router.get("/episodes/{episode_id}/script", response_model=ScriptResponse)
def get_script(
    episode_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> ScriptResponse:
    episode = _owned_episode(episode_id, db, user)
    script = db.scalar(
        select(Script)
        .where(Script.episode_id == episode.id)
        .order_by(Script.created_at.desc())
        .limit(1)
    )
    if script is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no script for this episode")
    return _script_out(db, script)


def _script_out(db: Session, script: Script) -> ScriptResponse:
    segments = list(
        db.scalars(
            select(ScriptSegment)
            .where(ScriptSegment.script_id == script.id)
            .order_by(ScriptSegment.ordinal)
        )
    )
    citations = _citations_for(db, "script_segment", [s.id for s in segments])
    return ScriptResponse(
        id=script.id,
        episode_id=script.episode_id,
        style_preset=script.style_preset,
        model_version=script.model_version,
        duration_minutes=script.duration_minutes,
        feedback=script.feedback,
        parent_script_id=script.parent_script_id,
        created_at=script.created_at,
        segments=[_segment_out(s, citations.get(s.id, [])) for s in segments],
    )


def _segment_out(segment: ScriptSegment, citations: list[CitationOut]) -> SegmentOut:
    return SegmentOut(
        id=segment.id,
        ordinal=segment.ordinal,
        segment_type=segment.segment_type,
        title=segment.title,
        start_minute=segment.start_minute,
        planned_minutes=segment.planned_minutes,
        topic_id=segment.topic_id,
        transition_in=segment.transition_in,
        host_script=segment.host_script,
        question=segment.question,
        deeper_questions=segment.deeper_questions,
        rationale=segment.rationale,
        expected_direction=segment.expected_direction,
        followups=segment.followups,
        risk_flags=segment.risk_flags,
        flagged_unsourced=segment.flagged_unsourced,
        edited_by_user=segment.edited_by_user,
        citations=citations,
    )


@router.patch("/scripts/{script_id}/segments/{segment_id}", response_model=SegmentOut)
def patch_segment(
    script_id: uuid.UUID,
    segment_id: uuid.UUID,
    body: PatchSegmentRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> SegmentOut:
    segment = db.get(ScriptSegment, segment_id)
    if segment is None or segment.script_id != script_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "segment not found")

    script = db.get(Script, script_id)
    _owned_episode(script.episode_id, db, user)

    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(segment, field, value)
    segment.edited_by_user = True
    db.commit()

    citations = _citations_for(db, "script_segment", [segment.id])
    return _segment_out(segment, citations.get(segment.id, []))
