"""Guest prep link.

A guest with little material online is the hardest case for research, and no
amount of searching fixes it. The remedy is the guest themselves: the host sends
one link, and the guest uploads a bio, a CV or notes, and says what they want to
be asked. Everything they send becomes an ordinary source, cited like any other.

The link is a capability URL: unguessable, needs no account, and is revoked by
clearing it. The guest never sees the dossier, the script, or anything else.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scripto.auth import current_user
from scripto.config import settings
from scripto.db import get_db
from scripto.jobs import queue
from scripto.jobs.limits import BudgetExceeded, SlotUnavailable
from scripto.llm import LLMError
from scripto.models import Episode, EpisodeSource, Source, User
from scripto.pipeline.discover import attach_source
from scripto.routes.episodes import (
    LINKEDIN_PROFILE_HINT,
    UPLOAD_TYPES,
    _attach_in_hand_source,
    _claim_citations,
    _is_linkedin_profile,
    _owned_episode,
)
from scripto.schemas import (
    PrepAnswersRequest,
    PrepLinkOut,
    PrepNotesRequest,
    PrepPageOut,
    PrepQuestion,
    PrepQuestionsRequest,
    PrepQuestionsResponse,
    PrepQuestionSuggestion,
    SourceOut,
)

router = APIRouter(tags=["prep"])


def _link_out(episode: Episode) -> PrepLinkOut:
    return PrepLinkOut(
        token=episode.prep_token or "",
        path=f"/prep/{episode.prep_token}",
        created_at=episode.prep_token_created_at,
    )


def _source_out(source: Source, added_by: str) -> SourceOut:
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


# --------------------------------------------------------------------------
# the host's side
# --------------------------------------------------------------------------


@router.post(
    "/episodes/{episode_id}/prep-questions/suggest", response_model=PrepQuestionsResponse
)
def suggest_questions(
    episode_id: uuid.UUID,
    style: str = "conversational",
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> PrepQuestionsResponse:
    """Draft the questionnaire from the research. Nothing is saved or sent."""
    from scripto.pipeline.prep import suggest_prep_questions

    episode = _owned_episode(episode_id, db, user)
    if episode.guest_entity_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "confirm the guest before drafting prep questions"
        )

    try:
        drafted = suggest_prep_questions(db, episode, style)
    except BudgetExceeded as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except SlotUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"drafting failed: {exc}") from exc
    db.commit()

    evidence = _claim_citations(db, [c for q in drafted for c in q["claim_ids"]])
    return PrepQuestionsResponse(
        episode_id=episode.id,
        style=style,
        questions=[
            PrepQuestionSuggestion(
                **q, citations=[evidence[c] for c in q["claim_ids"] if c in evidence]
            )
            for q in drafted
        ],
    )


@router.put("/episodes/{episode_id}/prep-questions", response_model=PrepQuestionsResponse)
def save_questions(
    episode_id: uuid.UUID,
    body: PrepQuestionsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> PrepQuestionsResponse:
    """The host's approved questionnaire. Editing it never changes the link."""
    episode = _owned_episode(episode_id, db, user)
    questions = [q for q in body.questions if q.text.strip()]
    if not questions:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "a questionnaire needs a question")

    episode.prep_style = body.style
    episode.prep_questions = [
        {
            "text": q.text.strip(),
            "why": q.why,
            "basis": q.basis,
            "claim_ids": q.claim_ids,
        }
        for q in questions
    ]
    db.commit()
    return PrepQuestionsResponse(
        episode_id=episode.id,
        style=episode.prep_style,
        questions=[PrepQuestionSuggestion(**q) for q in episode.prep_questions],
    )


@router.get("/episodes/{episode_id}/prep-questions", response_model=PrepQuestionsResponse)
def get_questions(
    episode_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> PrepQuestionsResponse:
    episode = _owned_episode(episode_id, db, user)
    return PrepQuestionsResponse(
        episode_id=episode.id,
        style=episode.prep_style or "conversational",
        questions=[PrepQuestionSuggestion(**q) for q in episode.prep_questions or []],
    )


@router.get("/episodes/{episode_id}/prep-link", response_model=PrepLinkOut | None)
def get_prep_link(
    episode_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> PrepLinkOut | None:
    episode = _owned_episode(episode_id, db, user)
    return _link_out(episode) if episode.prep_token else None


@router.post("/episodes/{episode_id}/prep-link", response_model=PrepLinkOut, status_code=201)
def create_prep_link(
    episode_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> PrepLinkOut:
    """Create the link, or return the existing one. Asking twice is not a mistake.

    Refused until the host has saved a questionnaire: nothing reaches a guest
    that the host has not read.
    """
    episode = _owned_episode(episode_id, db, user)
    if not episode.prep_questions:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "draft and save the prep questions before creating the link",
        )
    if not episode.prep_token:
        episode.prep_token = secrets.token_urlsafe(32)
        episode.prep_token_created_at = datetime.now(timezone.utc)
        episode.prep_published_at = datetime.now(timezone.utc)
        db.commit()
    return _link_out(episode)


@router.delete("/episodes/{episode_id}/prep-link", status_code=204)
def revoke_prep_link(
    episode_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Response:
    """Revoke the link. What the guest already sent stays; the link stops working."""
    episode = _owned_episode(episode_id, db, user)
    episode.prep_token = None
    episode.prep_token_created_at = None
    episode.prep_published_at = None
    db.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------
# the guest's side: no account, no auth
# --------------------------------------------------------------------------


def _episode_for_token(token: str, db: Session) -> Episode:
    episode = db.scalar(select(Episode).where(Episode.prep_token == token)) if token else None
    if episode is None:
        # A wrong token and a revoked one get the same answer: the link is
        # either live or it is not.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this prep link is not active")
    return episode


def _guest_sent(db: Session, episode: Episode) -> int:
    return (
        db.scalar(
            select(func.count(EpisodeSource.id)).where(
                EpisodeSource.episode_id == episode.id,
                EpisodeSource.removed_at.is_(None),
                EpisodeSource.added_by == "guest",
            )
        )
        or 0
    )


def _check_guest_allowance(db: Session, episode: Episode) -> None:
    """The guest gets their own allowance, separate from the host's."""
    if _guest_sent(db, episode) >= settings.max_sources_per_episode:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "thank you -- that is everything this interview needs. "
            "Contact your host if you have more to send.",
        )


def _rescore(db: Session, episode: Episode) -> None:
    """New material can change the coverage verdict."""
    queue.enqueue(
        db,
        kind="coverage_check",
        episode_id=episode.id,
        user_id=episode.user_id,
        payload={},
        delay_seconds=10,
    )


@router.get("/prep/{token}", response_model=PrepPageOut)
def prep_page(token: str, db: Session = Depends(get_db)) -> PrepPageOut:
    episode = _episode_for_token(token, db)
    return PrepPageOut(
        episode_title=episode.title,
        guest_name=episode.guest_name,
        style=episode.prep_style or "conversational",
        # Only the text: the host's reasoning and the claims behind a question
        # are research notes, not something the guest should see.
        questions=[PrepQuestion(text=q["text"]) for q in episode.prep_questions or []],
        submitted=_guest_sent(db, episode),
    )


@router.post("/prep/{token}/answers", response_model=SourceOut, status_code=201)
def prep_answers(
    token: str,
    body: PrepAnswersRequest,
    db: Session = Depends(get_db),
) -> SourceOut:
    """The guest's answers to the host's questionnaire.

    Stored as one source, so every line the host later writes from it carries a
    citation back to the guest's own words.
    """
    episode = _episode_for_token(token, db)
    _check_guest_allowance(db, episode)

    answered = [
        (a.question.strip(), a.answer.strip())
        for a in body.answers
        if a.question.strip() and a.answer.strip()
    ]
    if not answered:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "please answer at least one question")

    text = "\n\n".join(f"{question}\n\n{answer}" for question, answer in answered)
    source = _attach_in_hand_source(
        db,
        episode=episode,
        user_id=episode.user_id,
        raw=text.encode(),
        source_type="user_pasted",
        title=f"Prep answers from {episode.guest_name}",
        added_by="guest",
    )
    _rescore(db, episode)
    db.commit()
    return _source_out(source, "guest")


@router.post("/prep/{token}/upload", response_model=SourceOut, status_code=201)
async def prep_upload(
    token: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> SourceOut:
    """The guest uploads a CV, bio or transcript."""
    episode = _episode_for_token(token, db)
    _check_guest_allowance(db, episode)

    name = (file.filename or "").strip()
    suffix = PurePosixPath(name).suffix.lower()
    source_type = UPLOAD_TYPES.get(suffix)
    if source_type is None:
        raise HTTPException(
            415,
            f"cannot read {suffix or 'that file'}. Send a PDF, a Word document "
            "(.docx), or a .txt or .md file.",
        )

    raw = await file.read()
    if not raw.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "that file was empty")
    if len(raw) > settings.max_upload_bytes:
        limit = settings.max_upload_bytes // (1024 * 1024)
        raise HTTPException(413, f"that file is larger than the {limit} MB limit")

    source = _attach_in_hand_source(
        db,
        episode=episode,
        user_id=episode.user_id,
        raw=raw,
        source_type=source_type,
        title=name or None,
        added_by="guest",
    )
    _rescore(db, episode)
    db.commit()
    return _source_out(source, "guest")


@router.post("/prep/{token}/notes", response_model=SourceOut, status_code=201)
def prep_notes(
    token: str,
    body: PrepNotesRequest,
    db: Session = Depends(get_db),
) -> SourceOut:
    """The guest's own words: a bio, what they want to discuss, what to avoid.

    This is material no search can find, so it becomes a source like any other
    and is quoted with a citation the host can open.
    """
    episode = _episode_for_token(token, db)
    _check_guest_allowance(db, episode)

    sections: list[str] = []
    if body.bio and body.bio.strip():
        sections.append(f"About {episode.guest_name}, in their own words\n\n{body.bio.strip()}")
    if body.want_to_discuss and body.want_to_discuss.strip():
        sections.append(f"What they want to discuss\n\n{body.want_to_discuss.strip()}")
    if body.avoid and body.avoid.strip():
        sections.append(f"What they would rather avoid\n\n{body.avoid.strip()}")

    links = [link.strip() for link in body.links if link.strip()]
    for link in links:
        if _is_linkedin_profile(link):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, LINKEDIN_PROFILE_HINT)

    if not sections and not links:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "please write something or add a link before sending"
        )

    first: Source | None = None
    if sections:
        first = _attach_in_hand_source(
            db,
            episode=episode,
            user_id=episode.user_id,
            raw="\n\n".join(sections).encode(),
            source_type="user_pasted",
            title=f"Prep notes from {episode.guest_name}",
            added_by="guest",
        )

    for link in links:
        source = attach_source(
            db,
            episode=episode,
            url=link,
            added_by="guest",
            subject_entity_id=episode.guest_entity_id,
        )
        if source is None:
            continue
        first = first or source
        if source.status == "pending":
            queue.enqueue(
                db,
                kind="fetch_source",
                episode_id=episode.id,
                user_id=episode.user_id,
                payload={"source_id": str(source.id)},
                idempotency_key=f"fetch:{source.id}",
            )

    if first is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "none of those links could be read")

    _rescore(db, episode)
    db.commit()
    return _source_out(first, "guest")
