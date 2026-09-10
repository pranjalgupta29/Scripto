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
