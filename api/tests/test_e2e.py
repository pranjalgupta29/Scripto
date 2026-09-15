"""End to end run of the whole product, plus the section 9 acceptance criteria.

Runs against real Postgres and the real job queue, with fake providers standing
in for the network.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from scripto.models import (
    Chunk,
    Citation,
    Claim,
    ClaimCluster,
    DossierItem,
    Episode,
    Source,
)
from tests.drain import drain

LONG_TEXT = (
    "Dana Reyes joined Northwind Capital in 2016 as a junior analyst. "
    "She was promoted to head of research in 2021 after leading the energy desk. "
    "Reyes argues that private credit is structurally mispriced in the current cycle. "
    "She has said repeatedly that retail investors underestimate liquidity risk. "
    "In a 2024 panel she predicted consolidation among mid sized asset managers. "
    "Reyes tells a story about her first trade going badly wrong in 2017. "
    "She now sits on the investment committee at Northwind Capital. "
    "Her view is that regulation will tighten around private markets within two years. "
) * 4


def _episode_with_guest(client) -> str:
    created = client.post(
        "/episodes",
        json={
            "title": "Private credit with Dana Reyes",
            "guest_name": "Dana Reyes",
            "disambiguator": "Northwind Capital",
        },
    )
    assert created.status_code == 201, created.text
    episode_id = created.json()["id"]

    candidates = client.post(f"/episodes/{episode_id}/identify")
    assert candidates.status_code == 200, candidates.text
    body = candidates.json()

    # Identity is never auto-selected: the user must choose.
    assert len(body["candidates"]) >= 2
    assert body["candidates"][0]["confidence"] > body["candidates"][1]["confidence"]

    confirmed = client.post(
        f"/episodes/{episode_id}/confirm-guest", json={"candidate_index": 0}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "ingesting"
    assert confirmed.json()["guest"]["name"] == "Dana Reyes"
    return episode_id


def test_name_alone_is_rejected(auth_client):
    response = auth_client.post(
        "/episodes", json={"title": "Ep", "guest_name": "Dana Reyes", "disambiguator": ""}
    )
    assert response.status_code == 422


def test_full_run_to_script(auth_client, db):
    episode_id = _episode_with_guest(auth_client)

    # The user pastes a bio, which is the thin-footprint path in the spec.
    pasted = auth_client.post(
        f"/episodes/{episode_id}/sources",
        json={"text": LONG_TEXT, "title": "Dana Reyes bio"},
    )
    assert pasted.status_code == 201, pasted.text

    auth_client.post(
        f"/episodes/{episode_id}/topics",
        json={"topics": ["Private credit", "Liquidity risk", "Regulation"]},
    )

    drain()

    episode = db.get(Episode, uuid.UUID(episode_id))
    db.refresh(episode)
    assert episode.status in ("dossier_ready", "script_ready")
    assert episode.coverage_mode in ("rich", "thin", "sparse")

    # --- claims were extracted and every one points at a real span ---
    claims = list(db.scalars(select(Claim)))
    assert claims, "no claims extracted"
    for claim in claims:
        chunk = db.get(Chunk, claim.chunk_id)
        assert 0 <= claim.span_start < claim.span_end <= len(chunk.text)
        # The fabricated claim in the fake provider must never have survived.
        assert claim.text != "This assertion appears nowhere in the source text."

    # --- acceptance: zero unsourced sentences reach the UI ---
    dossier = auth_client.get(f"/episodes/{episode_id}/dossier").json()
    assert dossier["sections"], "dossier was empty"
    items_seen = 0
    for section in dossier["sections"]:
        for item in section["items"]:
            items_seen += 1
            assert item["citations"], f"unsourced item: {item['text']!r}"
            for citation in item["citations"]:
                assert citation["quote"].strip()
    assert items_seen > 0
    assert not db.scalars(
        select(DossierItem).where(DossierItem.flagged_unsourced.is_(True))
    ).all()

    # --- script generation ---
    created = auth_client.post(
        f"/episodes/{episode_id}/script", json={"style_preset": "conversational"}
    )
    assert created.status_code == 202, created.text
    drain()

    script = auth_client.get(f"/episodes/{episode_id}/script").json()
    assert script["segments"], "script had no segments"
    for segment in script["segments"]:
        assert segment["question"].strip()

    # --- user edits a segment inline ---
    segment_id = script["segments"][0]["id"]
    patched = auth_client.patch(
        f"/scripts/{script['id']}/segments/{segment_id}",
        json={"question": "What changed your mind about private credit?"},
    )
    assert patched.status_code == 200
    assert patched.json()["edited_by_user"] is True

    # --- export ---
    export = auth_client.get(f"/episodes/{episode_id}/export?format=pdf")
    assert export.status_code == 200
    assert export.content[:4] == b"%PDF"


def test_failed_source_degrades_but_never_fails_the_run(auth_client, db):
    """Acceptance: a failed source degrades the dossier, it never fails the run."""
    episode_id = _episode_with_guest(auth_client)

    auth_client.post(f"/episodes/{episode_id}/sources", json={"text": LONG_TEXT})
    # A URL that cannot be fetched in the test environment.
    auth_client.post(
        f"/episodes/{episode_id}/sources",
        json={"url": "https://nonexistent.invalid/definitely-not-here"},
    )
    auth_client.post(
        f"/episodes/{episode_id}/topics",
        json={"topics": ["Private credit", "Liquidity risk", "Regulation"]},
    )

    drain()

    failed = list(db.scalars(select(Source).where(Source.status == "failed")))
    assert failed, "expected the bad URL to be marked failed"
    assert failed[0].error

    episode = db.get(Episode, uuid.UUID(episode_id))
    db.refresh(episode)
    assert episode.status in ("dossier_ready", "script_ready")

    # The failure is surfaced to the user rather than hidden.
    listed = auth_client.get(f"/episodes/{episode_id}").json()
    assert any(s["status"] == "failed" and s["error"] for s in listed["sources"])


def test_sparse_guest_gets_labelled_dossier_never_an_empty_page(auth_client, db):
    """Acceptance: fewer than 3 usable sources produces a labelled thin/sparse result."""
    episode_id = _episode_with_guest(auth_client)
    auth_client.post(
        f"/episodes/{episode_id}/sources", json={"text": LONG_TEXT, "title": "only source"}
    )
    auth_client.post(
        f"/episodes/{episode_id}/topics",
        json={"topics": ["Private credit", "Liquidity risk", "Regulation"]},
    )
    drain()

    episode = db.get(Episode, uuid.UUID(episode_id))
    db.refresh(episode)
    assert episode.coverage_mode in ("thin", "sparse")

    detail = episode.coverage_detail
    assert detail["sources_parsed"] >= 1
    # The UI must be able to say what was missing and what to ask for.
    assert detail["missing"], "thin mode must tell the user what would help"

    dossier = auth_client.get(f"/episodes/{episode_id}/dossier").json()
    assert dossier["coverage_mode"] in ("thin", "sparse")
    assert dossier["sections"], "a thin guest must not produce an empty page"


def test_reparse_requires_no_network_fetch(auth_client, db, monkeypatch):
    """Acceptance: reparsing from stored blobs requires no network fetches."""
    episode_id = _episode_with_guest(auth_client)
    auth_client.post(f"/episodes/{episode_id}/sources", json={"text": LONG_TEXT})
    auth_client.post(
        f"/episodes/{episode_id}/topics",
        json={"topics": ["Private credit", "Liquidity risk", "Regulation"]},
    )
    drain()

    source = db.scalar(select(Source).where(Source.status == "parsed"))
    assert source is not None and source.blob_ref

    before = db.scalar(
        select(Chunk).where(Chunk.source_id == source.id).order_by(Chunk.ordinal)
    )
    assert before is not None

    # Any network call during reparse is a hard failure.
    import httpx

    def _boom(*args, **kwargs):
        raise AssertionError("reparse must not touch the network")

    monkeypatch.setattr(httpx, "get", _boom)

    from scripto.jobs.registry import get_handler
    from scripto.models import Job

    job = Job(
        id=uuid.uuid4(),
        kind="parse_source",
        episode_id=uuid.UUID(episode_id),
        payload={"source_id": str(source.id)},
    )
    get_handler("parse_source")(db, job)
    db.flush()

    after = db.scalar(
        select(Chunk).where(Chunk.source_id == source.id).order_by(Chunk.ordinal)
    )
    assert after is not None
    assert after.text == before.text


def test_already_covered_flags_a_repeated_story(auth_client, db):
    """Acceptance: the repeated-claim cluster is flagged for a guest with several appearances."""
    episode_id = _episode_with_guest(auth_client)

    # The same point restated across several distinct sources.
    repeated = (
        "Reyes has said that retail investors underestimate liquidity risk in private markets. "
    )
    for i in range(4):
        auth_client.post(
            f"/episodes/{episode_id}/sources",
            json={
                "text": repeated + f"This appearance number {i} also covered other ground. " * 6,
                "title": f"Appearance {i}",
            },
        )
    auth_client.post(
        f"/episodes/{episode_id}/topics",
        json={"topics": ["Private credit", "Liquidity risk", "Regulation"]},
    )
    drain()

    clusters = list(db.scalars(select(ClaimCluster).order_by(ClaimCluster.source_count.desc())))
    assert clusters, "no clusters formed"
    top = clusters[0]
    assert top.source_count >= 3, (
        f"expected a cluster spanning >=3 distinct sources, got {top.source_count}"
    )


def test_sources_are_global_and_deduped(auth_client, db):
    """Two episodes citing the same URL must share one source row, not refetch it."""
    first = _episode_with_guest(auth_client)
    url = "https://example.com/shared-article"
    auth_client.post(f"/episodes/{first}/sources", json={"url": url})

    second = auth_client.post(
        "/episodes",
        json={"title": "Second", "guest_name": "Dana Reyes", "disambiguator": "Northwind Capital"},
    ).json()["id"]
    auth_client.post(f"/episodes/{second}/sources", json={"url": url})

    matches = list(db.scalars(select(Source).where(Source.canonical_url.like("%shared-article%"))))
    assert len(matches) == 1, "the same URL must map to exactly one global source row"


def test_removing_a_source_is_per_episode(auth_client, db):
    episode_id = _episode_with_guest(auth_client)
    added = auth_client.post(
        f"/episodes/{episode_id}/sources", json={"text": LONG_TEXT}
    ).json()

    removed = auth_client.delete(f"/episodes/{episode_id}/sources/{added['id']}")
    assert removed.status_code == 204

    listed = auth_client.get(f"/episodes/{episode_id}").json()
    assert all(s["id"] != added["id"] for s in listed["sources"])
    # The global source row survives.
    assert db.get(Source, uuid.UUID(added["id"])) is not None


def test_cross_user_access_is_denied(client, auth_client):
    episode_id = _episode_with_guest(auth_client)

    other = client.post(
        "/auth/signup", json={"email": "other@example.com", "password": "supersecret1"}
    ).json()["access_token"]

    response = client.get(
        f"/episodes/{episode_id}", headers={"Authorization": f"Bearer {other}"}
    )
    assert response.status_code == 404


def test_unauthenticated_access_is_denied(client):
    assert client.get(f"/episodes/{uuid.uuid4()}").status_code == 401


def test_dossier_rebuilds_when_new_claims_arrive(auth_client, db, monkeypatch):
    """A build that ran before extraction must not block a later rebuild.

    The idempotency key that prevents duplicate work must not also prevent
    legitimate rebuilds, or an episode whose extraction lagged is stuck with an
    empty dossier forever.
    """
    from scripto.config import settings

    # Discovery fills the episode to the source cap, so leave headroom for the
    # source this test adds afterwards.
    monkeypatch.setattr(settings, "max_sources_per_episode", 100)

    episode_id = _episode_with_guest(auth_client)
    auth_client.post(
        f"/episodes/{episode_id}/topics",
        json={"topics": ["Private credit", "Liquidity risk", "Regulation"]},
    )

    # First pass with no sources at all: nothing to compose from.
    drain()
    first = auth_client.get(f"/episodes/{episode_id}/dossier").json()
    assert not first["sections"]

    # Now real material arrives, exactly as it would if extraction had lagged.
    auth_client.post(f"/episodes/{episode_id}/sources", json={"text": LONG_TEXT})
    drain()

    rebuilt = auth_client.get(f"/episodes/{episode_id}/dossier").json()
    assert rebuilt["sections"], "dossier never rebuilt after claims arrived"
    assert any(item["citations"] for s in rebuilt["sections"] for item in s["items"])


# --------------------------------------------------------------------------
# topic suggestions and the timed run-of-show
# --------------------------------------------------------------------------

HOST_TOPICS = ["Private credit", "Liquidity risk", "Regulation"]


def _ready_episode(auth_client) -> str:
    episode_id = _episode_with_guest(auth_client)
    auth_client.post(
        f"/episodes/{episode_id}/sources", json={"text": LONG_TEXT, "title": "Dana Reyes bio"}
    )
    auth_client.post(f"/episodes/{episode_id}/topics", json={"topics": HOST_TOPICS})
    drain()
    return episode_id


def test_saved_topics_can_be_read_back(auth_client):
    episode_id = _episode_with_guest(auth_client)
    auth_client.post(f"/episodes/{episode_id}/topics", json={"topics": HOST_TOPICS})
    topics = auth_client.get(f"/episodes/{episode_id}/topics").json()
    assert [t["text"] for t in topics] == HOST_TOPICS


def test_topic_suggestions_say_what_backs_them(auth_client):
    """A suggestion is either researched, with evidence, or labelled title-only."""
    episode_id = _ready_episode(auth_client)

    response = auth_client.post(f"/episodes/{episode_id}/topics/suggest")
    assert response.status_code == 200, response.text
    suggestions = response.json()["suggestions"]
    assert suggestions

    researched = [s for s in suggestions if s["basis"] == "research"]
    title_only = [s for s in suggestions if s["basis"] == "title"]
    assert researched, "expected suggestions grounded in the dossier"
    for s in researched:
        assert s["claim_ids"] and s["citations"], "researched suggestions must show evidence"
        assert s["citations"][0]["quote"].strip()
    for s in title_only:
        assert not s["claim_ids"]

    existing = {t.lower() for t in HOST_TOPICS}
    assert not any(s["text"].lower() in existing for s in suggestions)


def test_suggestions_need_a_confirmed_guest(auth_client):
    created = auth_client.post(
        "/episodes",
        json={"title": "Ep", "guest_name": "Dana Reyes", "disambiguator": "Northwind Capital"},
    ).json()
    response = auth_client.post(f"/episodes/{created['id']}/topics/suggest")
    assert response.status_code == 409


def test_script_is_a_timed_run_of_show(auth_client):
    episode_id = _ready_episode(auth_client)
    created = auth_client.post(
        f"/episodes/{episode_id}/script",
        json={"style_preset": "conversational", "duration_minutes": 60},
    )
    assert created.status_code == 202, created.text
    drain()

    script = auth_client.get(f"/episodes/{episode_id}/script").json()
    assert script["duration_minutes"] == 60
    segments = script["segments"]
    timeline = [s for s in segments if s["segment_type"] != "bonus"]

    assert timeline[0]["segment_type"] == "opening"
    assert timeline[-1]["segment_type"] == "closing"
    topic_blocks = [s for s in timeline if s["segment_type"] == "topic"]
    assert len(topic_blocks) == len(HOST_TOPICS)

    # The blocks add up to the booked hour with no gaps or overlaps.
    assert sum(s["planned_minutes"] for s in timeline) == 60
    clock = 0
    for block in timeline:
        assert block["start_minute"] == clock
        clock += block["planned_minutes"]

    # Flow: every topic block has a spoken transition into it.
    assert all(block["transition_in"] for block in topic_blocks)
    # Backup topics sit outside the timeline.
    backups = [s for s in segments if s["segment_type"] == "bonus"]
    assert backups and all(b["start_minute"] is None for b in backups)


def test_host_order_is_kept_when_asked(auth_client):
    episode_id = _ready_episode(auth_client)
    auth_client.post(
        f"/episodes/{episode_id}/script",
        json={"style_preset": "formal", "duration_minutes": 45, "optimize_order": False},
    )
    drain()
    script = auth_client.get(f"/episodes/{episode_id}/script").json()
    topics = auth_client.get(f"/episodes/{episode_id}/topics").json()
    block_topics = [s["topic_id"] for s in script["segments"] if s["segment_type"] == "topic"]
    assert block_topics == [t["id"] for t in topics]


def test_longer_interview_gets_more_questions(auth_client):
    episode_id = _ready_episode(auth_client)

    def question_count(minutes: int) -> int:
        auth_client.post(
            f"/episodes/{episode_id}/script",
            json={"style_preset": "conversational", "duration_minutes": minutes},
        )
        drain()
        script = auth_client.get(f"/episodes/{episode_id}/script").json()
        return sum(
            1 + len(s["deeper_questions"])
            for s in script["segments"]
            if s["segment_type"] == "topic"
        )

    assert question_count(90) > question_count(30)


# --------------------------------------------------------------------------
# progress, source allowances, topic research, search budget, regenerate
# --------------------------------------------------------------------------


def _article_html(url: str) -> bytes:
    """A readable article whose bytes differ per URL (identical bytes are deduped)."""
    paragraph = (
        "Researchers studying private credit published new findings on liquidity risk this year. "
        "Several economists argue that retail investors misjudge how quickly funds can be redeemed. "
        "Regulators are weighing tighter disclosure rules for private markets. "
        f"This report was published at {url} for readers following the debate. "
    ) * 3
    return (
        f"<html><head><title>Private markets report {url}</title></head><body><article>"
        f"<h1>Private markets report</h1><p>{paragraph}</p><p>{paragraph}</p>"
        "</article></body></html>"
    ).encode()


def test_waiting_coverage_check_adds_no_rows(auth_client, db):
    """The progress bar climbed because a waiting check added a job every 10s."""
    from sqlalchemy import func

    from scripto.models import Job

    episode_id = _ready_episode(auth_client)
    checks = db.scalar(
        select(func.count(Job.id)).where(
            Job.episode_id == uuid.UUID(episode_id), Job.kind == "coverage_check"
        )
    )
    # One per discovery run plus one per source the host added, not one per poll.
    assert checks <= 4, f"{checks} coverage_check rows for one episode"


def test_progress_counts_sources_not_jobs(auth_client):
    episode_id = _ready_episode(auth_client)
    body = auth_client.get(f"/episodes/{episode_id}").json()
    progress = body["progress"]
    assert progress["sources_total"] == len(body["sources"])
    assert progress["sources_read"] == progress["sources_total"]
    assert progress["sources_analysed"] == progress["sources_total"]
    assert progress["pending"] == 0 and progress["stage"] is None


def test_host_can_add_sources_after_discovery_fills_its_allowance(auth_client, monkeypatch):
    """Discovered sources must never block the host from pasting a bio."""
    from scripto.config import settings

    monkeypatch.setattr(settings, "max_sources_per_episode", 3)
    episode_id = _episode_with_guest(auth_client)
    drain()
    assert len(auth_client.get(f"/episodes/{episode_id}").json()["sources"]) >= 3

    added = auth_client.post(
        f"/episodes/{episode_id}/sources", json={"text": LONG_TEXT, "title": "Bio"}
    )
    assert added.status_code == 201, added.text


def test_topic_research_searches_the_topics_and_builds_a_brief(auth_client, db, monkeypatch):
    from sqlalchemy import func

    from scripto.adapters.web_article import WebArticleAdapter
    from scripto.config import settings
    from scripto.models import Job
    from scripto.search import FakeSearchProvider

    monkeypatch.setattr(settings, "max_sources_per_episode", 3)
    monkeypatch.setattr(WebArticleAdapter, "fetch", lambda self, url: _article_html(url))
    queries: list[str] = []
    original = FakeSearchProvider.search

    def recording(self, query, *, limit=10):
        queries.append(query)
        return original(self, query, limit=limit)

    monkeypatch.setattr(FakeSearchProvider, "search", recording)

    episode_id = _episode_with_guest(auth_client)
    auth_client.post(f"/episodes/{episode_id}/topics", json={"topics": HOST_TOPICS})
    drain()

    episode = db.get(Episode, uuid.UUID(episode_id))
    db.refresh(episode)
    assert episode.coverage_mode in ("thin", "sparse")
    assert episode.topic_entity_id is not None

    # It searched for the topics, not for the guest again.
    topic_queries = [q for q in queries if any(t in q for t in HOST_TOPICS)]
    assert topic_queries, "topic research never searched for the topics"
    assert not any("Dana Reyes" in q for q in topic_queries)

    topic_claims = db.scalar(
        select(func.count(Claim.id)).where(Claim.subject_entity_id == episode.topic_entity_id)
    )
    assert topic_claims, "no claims were attributed to the topic"

    dossier = auth_client.get(f"/episodes/{episode_id}/dossier").json()
    assert "topic_brief" in {s["section"] for s in dossier["sections"]}

    # Topic articles are labelled as such and never count toward the guest's label.
    listed = auth_client.get(f"/episodes/{episode_id}").json()["sources"]
    guest_listed = [s for s in listed if s["subject"] == "guest"]
    assert any(s["subject"] == "topic" for s in listed) and guest_listed
    assert episode.coverage_detail["sources_parsed"] == sum(
        s["status"] == "parsed" for s in guest_listed
    )

    # Clustering ran once per wave of claims, not once per source.
    guest = str(episode.guest_entity_id)
    runs = lambda kind: db.scalar(  # noqa: E731
        select(func.count(Job.id)).where(
            Job.kind == kind, Job.payload["subject_entity_id"].astext == guest
        )
    )
    assert runs("extract_claims") >= 3
    assert runs("cluster_claims") < runs("extract_claims")


def test_search_budget_counts_every_search(auth_client, monkeypatch):
    """A whole discovery run used to be charged as a single search."""
    from scripto.config import settings
    from scripto.jobs.limits import usage
    from scripto.search import FakeSearchProvider

    monkeypatch.setattr(settings, "budget_search_calls_per_month", 10_000)
    calls: list[str] = []
    original = FakeSearchProvider.search

    def counting(self, query, *, limit=10):
        calls.append(query)
        return original(self, query, limit=limit)

    monkeypatch.setattr(FakeSearchProvider, "search", counting)

    _episode_with_guest(auth_client)
    drain()
    used, _ = usage("search")
    assert len(calls) > 2
    assert used == len(calls)


def test_regenerate_keeps_the_hosts_edits(auth_client):
    episode_id = _ready_episode(auth_client)
    body = {"style_preset": "conversational", "duration_minutes": 45}
    auth_client.post(f"/episodes/{episode_id}/script", json=body)
    drain()
    first = auth_client.get(f"/episodes/{episode_id}/script").json()
    block = next(s for s in first["segments"] if s["segment_type"] == "topic")
    auth_client.patch(
        f"/scripts/{first['id']}/segments/{block['id']}",
        json={"question": "My own question about this topic?"},
    )

    auth_client.post(f"/episodes/{episode_id}/script", json=body)
    drain()
    second = auth_client.get(f"/episodes/{episode_id}/script").json()
    assert second["id"] != first["id"]
    assert second["parent_script_id"] == first["id"]
    same = next(s for s in second["segments"] if s["topic_id"] == block["topic_id"])
    assert same["question"] == "My own question about this topic?"
    assert same["edited_by_user"] is True


def test_regenerate_with_a_note_revises_the_current_version(auth_client, monkeypatch):
    from scripto.llm.fake import FakeProvider

    prompts: list[str] = []
    original = FakeProvider._task_generate_script

    def recording(self, prompt):
        prompts.append(prompt)
        return original(self, prompt)

    monkeypatch.setattr(FakeProvider, "_task_generate_script", recording)

    episode_id = _ready_episode(auth_client)
    auth_client.post(f"/episodes/{episode_id}/script", json={"style_preset": "conversational"})
    drain()
    first_id = auth_client.get(f"/episodes/{episode_id}/script").json()["id"]

    note = "Spend more time on liquidity risk and keep questions shorter."
    auth_client.post(
        f"/episodes/{episode_id}/script",
        json={"style_preset": "conversational", "feedback": note},
    )
    drain()

    second = auth_client.get(f"/episodes/{episode_id}/script").json()
    assert second["feedback"] == note
    assert second["parent_script_id"] == first_id
    assert note in prompts[-1], "the host's note never reached the model"
    assert "Current version of this run-of-show" in prompts[-1]


def _stub_article_fetch(monkeypatch):
    from scripto.adapters.web_article import WebArticleAdapter

    monkeypatch.setattr(WebArticleAdapter, "fetch", lambda self, url: _article_html(url))


def _record_searches(monkeypatch) -> list[str]:
    from scripto.search import FakeSearchProvider

    queries: list[str] = []
    original = FakeSearchProvider.search

    def recording(self, query, *, limit=10):
        queries.append(query)
        return original(self, query, limit=limit)

    monkeypatch.setattr(FakeSearchProvider, "search", recording)
    return queries


def test_well_covered_guests_also_get_a_topic_brief(auth_client, db, monkeypatch):
    """The host's call: topic research runs for every episode, not only thin ones."""
    import scripto.pipeline.coverage as coverage
    from scripto.config import settings

    monkeypatch.setattr(settings, "max_sources_per_episode", 3)
    monkeypatch.setattr(coverage, "_mode", lambda *args: "rich")
    _stub_article_fetch(monkeypatch)
    queries = _record_searches(monkeypatch)

    episode_id = _episode_with_guest(auth_client)
    auth_client.post(f"/episodes/{episode_id}/topics", json={"topics": HOST_TOPICS})
    drain()

    episode = db.get(Episode, uuid.UUID(episode_id))
    db.refresh(episode)
    assert episode.coverage_mode == "rich"
    assert any("Private credit" in q for q in queries), "topics were not researched"
    dossier = auth_client.get(f"/episodes/{episode_id}/dossier").json()
    assert "topic_brief" in {s["section"] for s in dossier["sections"]}


def test_editing_topics_researches_only_the_new_ones(auth_client, db, monkeypatch):
    from scripto.config import settings
    from scripto.models import Entity

    monkeypatch.setattr(settings, "max_sources_per_episode", 3)
    _stub_article_fetch(monkeypatch)
    queries = _record_searches(monkeypatch)

    episode_id = _episode_with_guest(auth_client)
    auth_client.post(f"/episodes/{episode_id}/topics", json={"topics": HOST_TOPICS})
    drain()
    first_round = len(queries)

    # Swap one topic: only the new one should be searched.
    edited = ["Private credit", "Liquidity risk", "Pension fund exposure"]
    auth_client.post(f"/episodes/{episode_id}/topics", json={"topics": edited})
    drain()
    second_round = queries[first_round:]

    assert any("Pension fund exposure" in q for q in second_round)
    assert not any("Private credit" in q or "Liquidity risk" in q for q in second_round)

    episode = db.get(Episode, uuid.UUID(episode_id))
    db.refresh(episode)
    topic = db.get(Entity, episode.topic_entity_id)
    assert set(topic.external_ids["researched_topics"]) == set(HOST_TOPICS) | {"Pension fund exposure"}
    assert topic.aliases == edited

    # Saving the same topics again costs no searches at all.
    before = len(queries)
    auth_client.post(f"/episodes/{episode_id}/topics", json={"topics": edited})
    drain()
    assert len(queries) == before


def test_topic_research_gives_every_topic_its_share(auth_client, monkeypatch):
    """One shared allowance let the first topic's searches fill every slot."""
    from scripto.config import settings

    monkeypatch.setattr(settings, "max_sources_per_episode", 3)
    _stub_article_fetch(monkeypatch)
    _record_searches(monkeypatch)

    episode_id = _episode_with_guest(auth_client)
    auth_client.post(f"/episodes/{episode_id}/topics", json={"topics": HOST_TOPICS})
    drain()

    listed = auth_client.get(f"/episodes/{episode_id}").json()["sources"]
    topic_urls = " ".join(s["url"] or "" for s in listed if s["subject"] == "topic")
    for slug in ("private-credit", "liquidity-risk", "regulation"):
        assert slug in topic_urls, f"no source was researched for {slug}"


def test_topic_sources_are_read_against_their_own_topic(auth_client, db, monkeypatch):
    """Later topics' pages were read against a label naming only the first three."""
    import scripto.pipeline.extract as extract
    from scripto.config import settings
    from scripto.models import EpisodeSource

    monkeypatch.setattr(settings, "max_sources_per_episode", 3)
    _stub_article_fetch(monkeypatch)
    subjects: list[str] = []
    original = extract.extract_from_chunk

    def recording(chunk, subject_name):
        subjects.append(subject_name)
        return original(chunk, subject_name)

    monkeypatch.setattr(extract, "extract_from_chunk", recording)

    episode_id = _episode_with_guest(auth_client)
    auth_client.post(f"/episodes/{episode_id}/topics", json={"topics": HOST_TOPICS})
    drain()

    for topic in HOST_TOPICS:
        assert topic in subjects, f"no source was read against {topic!r}"
    labels = db.scalars(
        select(EpisodeSource.topic).where(
            EpisodeSource.episode_id == uuid.UUID(episode_id),
            EpisodeSource.topic.is_not(None),
        )
    ).all()
    assert set(labels) == set(HOST_TOPICS)
    listed = auth_client.get(f"/episodes/{episode_id}").json()["sources"]
    assert {s["topic"] for s in listed if s["subject"] == "topic"} == set(HOST_TOPICS)


def test_topic_brief_offers_every_topic_a_share(auth_client, monkeypatch):
    """Newest-first let the first topic fill the brief: 44 of 60 claims on one topic."""
    import scripto.pipeline.dossier as dossier
    from scripto.config import settings

    monkeypatch.setattr(settings, "max_sources_per_episode", 6)
    monkeypatch.setattr(dossier, "MAX_CLAIMS_PER_SECTION", 6)
    _stub_article_fetch(monkeypatch)
    prompts: list[str] = []
    original = dossier.dossier_prompt

    def recording(*args, **kwargs):
        prompt = original(*args, **kwargs)
        prompts.append(prompt)
        return prompt

    monkeypatch.setattr(dossier, "dossier_prompt", recording)

    episode_id = _episode_with_guest(auth_client)
    auth_client.post(f"/episodes/{episode_id}/topics", json={"topics": HOST_TOPICS})
    drain()

    brief = next(p for p in reversed(prompts) if "Section: topic_brief" in p)
    offered = {
        block.splitlines()[0]: sum(line.startswith("[") for line in block.splitlines())
        for block in brief.split("\nTopic: ")[1:]
    }
    assert offered == {topic: 2 for topic in HOST_TOPICS}


def test_a_topic_with_nothing_sourced_is_reported(auth_client, monkeypatch):
    """A topic that yields no claims is named, not quietly left out of the brief."""
    import scripto.pipeline.extract as extract
    from scripto.config import settings

    monkeypatch.setattr(settings, "max_sources_per_episode", 3)
    _stub_article_fetch(monkeypatch)
    original = extract.extract_from_chunk
    monkeypatch.setattr(
        extract,
        "extract_from_chunk",
        lambda chunk, subject_name: (
            [] if subject_name == "Regulation" else original(chunk, subject_name)
        ),
    )

    episode_id = _episode_with_guest(auth_client)
    auth_client.post(f"/episodes/{episode_id}/topics", json={"topics": HOST_TOPICS})
    drain()

    dossier = auth_client.get(f"/episodes/{episode_id}/dossier").json()
    assert dossier["coverage_detail"]["topic_gaps"] == ["Regulation"]
