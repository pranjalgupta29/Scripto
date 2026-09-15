"""5.1 Identify.

Search, then an LLM ranking pass. The user picks. Wrong identity poisons
everything downstream, so this step is never skipped and never auto-selected.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from scripto.jobs.limits import provider_slot
from scripto.jobs.registry import register
from scripto.llm import get_llm
from scripto.llm.prompts import IDENTIFY_SCHEMA, IDENTIFY_SYSTEM, identify_prompt
from scripto.models import Entity, Episode, Job
from scripto.search import get_search


def identify_candidates(name: str, disambiguator: str) -> list[dict]:
    search = get_search()
    queries = [f'"{name}" {disambiguator}', f'"{name}" profile bio']

    results: list[dict] = []
    seen: set[str] = set()
    for query in queries:
        # One slot per search, so budgets and pacing count real calls.
        with provider_slot("search"):
            found = search.search(query, limit=8)
        for r in found:
            if r.url in seen:
                continue
            seen.add(r.url)
            results.append({"url": r.url, "title": r.title, "snippet": r.snippet})

    if not results:
        return []

    with provider_slot("llm"):
        payload = get_llm().complete_json(
            role="rank",
            system=IDENTIFY_SYSTEM,
            prompt=identify_prompt(name, disambiguator, results),
            schema=IDENTIFY_SCHEMA,
        )

    candidates = payload.get("candidates", [])
    candidates.sort(key=lambda c: c.get("confidence", 0), reverse=True)
    return candidates[:5]


@register("identify")
def run_identify(db: Session, job: Job) -> None:
    """Store candidates on the episode for the user to choose from."""
    episode = db.get(Episode, job.episode_id)
    if episode is None:
        return

    candidates = identify_candidates(episode.guest_name, episode.disambiguator)
    episode.coverage_detail = {**(episode.coverage_detail or {}), "candidates": candidates}
    episode.status = "identifying"
    db.flush()


def confirm_guest(db: Session, episode: Episode, candidate: dict) -> Entity:
    """Freeze the chosen identity into an entity row."""
    entity = Entity(
        id=uuid.uuid4(),
        type="person",
        name=candidate["name"],
        aliases=[],
        external_ids={"evidence_urls": candidate.get("evidence_urls", [])},
        headline=candidate.get("headline"),
        employer=candidate.get("employer"),
        photo_url=candidate.get("photo_url"),
    )
    db.add(entity)
    db.flush()

    episode.guest_entity_id = entity.id
    episode.status = "ingesting"
    db.flush()
    return entity
