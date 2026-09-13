"""PDF export. Every line carries its source, same as the UI."""

from __future__ import annotations

import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import select
from sqlalchemy.orm import Session

from scripto.auth import current_user
from scripto.db import get_db
from scripto.models import DossierItem, Episode, Script, ScriptSegment, User
from scripto.routes.episodes import _citations_for, _owned_episode

router = APIRouter(tags=["export"])

SECTION_TITLES = {
    "career_timeline": "Career timeline",
    "recent_news": "Recent news",
    "public_positions": "Public positions",
    "already_covered": "Already covered elsewhere",
    "unexplored_angles": "Unexplored angles",
    "topic_brief": "Topic and industry brief",
}


@router.get("/episodes/{episode_id}/export")
def export_episode(
    episode_id: uuid.UUID,
    format: str = Query("pdf", pattern="^(pdf)$"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Response:
    episode = _owned_episode(episode_id, db, user)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        title=episode.title,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch,
    )

    styles = getSampleStyleSheet()
    cite_style = ParagraphStyle(
        "Citation", parent=styles["BodyText"], fontSize=7.5, textColor="#666666", leftIndent=12
    )

    story: list = [
        Paragraph(_esc(episode.title), styles["Title"]),
        Paragraph(
            f"Guest: {_esc(episode.guest.name if episode.guest else episode.guest_name)}",
            styles["Heading3"],
        ),
    ]

    # Never present a thin result as though it were a full one.
    if episode.coverage_mode in ("thin", "sparse"):
        detail = episode.coverage_detail or {}
        story.append(
            Paragraph(
                f"<b>Limited public coverage ({_esc(episode.coverage_mode)}).</b> "
                f"Built from {detail.get('sources_parsed', 0)} usable source(s). "
                "Treat guest specifics as incomplete.",
                styles["BodyText"],
            )
        )
    story.append(Spacer(1, 12))

    items = list(
        db.scalars(
            select(DossierItem)
            .where(DossierItem.episode_id == episode.id)
            .order_by(DossierItem.section, DossierItem.ordinal)
        )
    )
    citations = _citations_for(db, "dossier_item", [i.id for i in items])

    grouped: dict[str, list[DossierItem]] = {}
    for item in items:
        grouped.setdefault(item.section, []).append(item)

    for section, section_items in grouped.items():
        story.append(Paragraph(SECTION_TITLES.get(section, section), styles["Heading2"]))
        for item in section_items:
            story.append(Paragraph(_esc(item.text), styles["BodyText"]))
            for citation in citations.get(item.id, [])[:3]:
                label = citation.source_title or citation.source_url or "source"
                story.append(Paragraph(f"— {_esc(label)}", cite_style))
        story.append(Spacer(1, 10))

    script = db.scalar(
        select(Script)
        .where(Script.episode_id == episode.id)
        .order_by(Script.created_at.desc())
        .limit(1)
    )
    if script is not None:
        heading = "Script"
        if script.duration_minutes:
            heading = f"Script: {script.duration_minutes}-minute run of show"
        story.append(Paragraph(_esc(heading), styles["Heading2"]))
        segments = list(
            db.scalars(
                select(ScriptSegment)
                .where(ScriptSegment.script_id == script.id)
                .order_by(ScriptSegment.ordinal)
            )
        )
        main = [s for s in segments if s.segment_type != "bonus"]
        backup = [s for s in segments if s.segment_type == "bonus"]
        for segment in main:
            _render_segment(story, segment, styles, cite_style)
        if backup:
            story.append(Paragraph("Backup topics (if time allows)", styles["Heading3"]))
            for segment in backup:
                _render_segment(story, segment, styles, cite_style)

    if len(story) <= 3:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing to export yet")

    doc.build(story)
    filename = f"{episode.title.replace(' ', '-').lower()[:60]}.pdf"
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _esc(text: str) -> str:
    """reportlab treats its input as mini-HTML, so escape user content."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _clock(minute: int) -> str:
    return f"{minute // 60}:{minute % 60:02d}"


def _render_segment(story: list, segment: ScriptSegment, styles, cite_style) -> None:
    heading = segment.title or segment.segment_type.title()
    if segment.start_minute is not None and segment.planned_minutes:
        end = segment.start_minute + segment.planned_minutes
        heading = f"{_clock(segment.start_minute)}-{_clock(end)}  {heading}"
    story.append(Paragraph(_esc(heading), styles["Heading4"]))

    if segment.transition_in:
        story.append(
            Paragraph(f"<i>Transition:</i> {_esc(segment.transition_in)}", styles["BodyText"])
        )
    if segment.host_script:
        story.append(Paragraph(f"<i>Say:</i> {_esc(segment.host_script)}", styles["BodyText"]))
    story.append(Paragraph(f"<b>Q.</b> {_esc(segment.question)}", styles["BodyText"]))
    if segment.deeper_questions:
        story.append(
            ListFlowable(
                [
                    ListItem(Paragraph(_esc(str(q)), styles["BodyText"]))
                    for q in segment.deeper_questions
                ],
                bulletType="1",
            )
        )
    if segment.rationale:
        story.append(Paragraph(f"<i>Why:</i> {_esc(segment.rationale)}", cite_style))
    if segment.followups:
        story.append(
            ListFlowable(
                [ListItem(Paragraph(_esc(str(f)), cite_style)) for f in segment.followups],
                bulletType="bullet",
            )
        )
    if segment.risk_flags:
        flags = ", ".join(str(f) for f in segment.risk_flags)
        story.append(Paragraph(f"<b>Flags:</b> {_esc(flags)}", cite_style))
    if segment.flagged_unsourced:
        story.append(
            Paragraph("<b>Not backed by the research.</b> Check before asking.", cite_style)
        )
    story.append(Spacer(1, 8))
