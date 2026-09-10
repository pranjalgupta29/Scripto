"""Unit tests for the guards and primitives the pipeline depends on."""

from __future__ import annotations

import uuid

import pytest

from scripto.adapters.base import canonicalize_url
from scripto.adapters.youtube import video_id
from scripto.jobs import queue
from scripto.models import Episode, User
from scripto.pipeline.chunking import chunk_segments, chunk_text
from scripto.pipeline.extract import validate_claim


# --------------------------------------------------------------------------
# the anti-fabrication guard
# --------------------------------------------------------------------------

CHUNK = "Alice joined Acme in 2019. She now leads the research desk there."


def test_valid_span_is_kept():
    claim = validate_claim(
        {"text": "Alice joined Acme in 2019.", "kind": "biographical", "verbatim_span": [0, 26]},
        CHUNK,
    )
    assert claim is not None
    assert CHUNK[claim["span_start"] : claim["span_end"]] == "Alice joined Acme in 2019."


@pytest.mark.parametrize(
    "span",
    [
        [0, 9999],  # runs past the end of the chunk
        [-5, 20],  # negative start
        [30, 10],  # inverted
        [10, 12],  # too short to support anything
        "nonsense",  # not a span at all
    ],
)
def test_unresolvable_spans_are_dropped(span):
    assert (
        validate_claim(
            {"text": "A claim.", "kind": "fact", "verbatim_span": span}, CHUNK
        )
        is None
    )


def test_invalid_kind_is_dropped():
    assert (
        validate_claim(
            {"text": "A claim.", "kind": "speculation", "verbatim_span": [0, 26]}, CHUNK
        )
        is None
    )


# --------------------------------------------------------------------------
# chunking carries exact positions
# --------------------------------------------------------------------------


def test_text_chunks_carry_resolvable_offsets():
    text = " ".join(f"Sentence number {i} about the guest and their work." for i in range(200))
    chunks = chunk_text(text)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk["start_offset"] is not None and chunk["end_offset"] is not None
        # The offsets must actually resolve to the chunk's own text.
        assert text[chunk["start_offset"] : chunk["end_offset"]] == chunk["text"]
        assert "start_ms" not in chunk


def test_av_chunks_carry_ms_ranges_not_offsets():
    segments = [
        {"text": f"caption cue {i}", "start_ms": i * 1000, "end_ms": (i + 1) * 1000, "speaker": None}
        for i in range(500)
    ]
    chunks = chunk_segments(segments)

    assert chunks
    for chunk in chunks:
        assert chunk["start_ms"] is not None and chunk["end_ms"] is not None
        assert chunk["end_ms"] > chunk["start_ms"]
        assert "start_offset" not in chunk


# --------------------------------------------------------------------------
# url handling
# --------------------------------------------------------------------------


def test_canonicalization_dedupes_equivalent_urls():
    variants = [
        "https://www.example.com/post?utm_source=twitter&id=3",
        "http://example.com/post/?id=3",
        "https://EXAMPLE.com/post?id=3&fbclid=xyz",
    ]
    assert len({canonicalize_url(u) for u in variants}) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
    ],
)
def test_video_id_extraction(url):
    assert video_id(url) == "dQw4w9WgXcQ"


# --------------------------------------------------------------------------
# queue behaviour
# --------------------------------------------------------------------------


def _user(db, email="q@example.com"):
    user = User(id=uuid.uuid4(), email=email, password_hash="x")
    db.add(user)
    db.flush()
    return user


def test_idempotency_key_blocks_duplicate_jobs(db):
    user = _user(db)
    first = queue.enqueue(db, kind="discover", user_id=user.id, idempotency_key="k1")
    second = queue.enqueue(db, kind="discover", user_id=user.id, idempotency_key="k1")

    assert first is not None
    assert second is None


def test_dequeue_is_fair_across_users(db):
    """A user with a job already running must not be served before an idle user."""
    busy = _user(db, "busy@example.com")
    idle = _user(db, "idle@example.com")

    # Busy user queued first and already has one running.
    queue.enqueue(db, kind="discover", user_id=busy.id, idempotency_key="busy-running")
    running = queue.dequeue(db, worker_id="w0")
    assert running is not None

    queue.enqueue(db, kind="discover", user_id=busy.id, idempotency_key="busy-queued")
    queue.enqueue(db, kind="discover", user_id=idle.id, idempotency_key="idle-queued")
    db.commit()

    # Pure FIFO would pick the busy user's older job. Fairness picks the idle user.
    claimed = queue.dequeue(db, worker_id="w1")
    assert claimed is not None
    assert claimed.user_id == idle.id


def test_expired_lease_is_requeued(db):
    user = _user(db)
    queue.enqueue(db, kind="discover", user_id=user.id, idempotency_key="lease")
    job = queue.dequeue(db, worker_id="crashed")
    assert job.state == "running"

    # Simulate a crashed worker: the lease lapses and is never heartbeat.
    from datetime import datetime, timedelta, timezone

    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    assert queue.sweep_expired_leases(db) == 1
    db.expire_all()
    assert db.get(type(job), job.id).state == "queued"


def test_failure_backs_off_then_dies(db):
    from scripto.config import settings

    user = _user(db)
    queue.enqueue(db, kind="discover", user_id=user.id, idempotency_key="dies")

    for _ in range(settings.job_max_attempts):
        job = queue.dequeue(db, worker_id="w")
        assert job is not None
        queue.fail(db, job, "boom")
        job.next_attempt_at = job.created_at  # skip the backoff wait
        db.commit()

    assert job.state == "dead"
    assert queue.dequeue(db, worker_id="w") is None


# --------------------------------------------------------------------------
# quote location: citations must point at text that supports the claim
# --------------------------------------------------------------------------

QUOTE_CHUNK = (
    "Nadella became chief executive of Microsoft in 2014.\n"
    "He published Hit Refresh in 2017, describing a cultural reset."
)


def test_located_quote_returns_its_own_text():
    from scripto.pipeline.extract import locate_quote

    span = locate_quote("published Hit Refresh in 2017", QUOTE_CHUNK)
    assert span is not None
    assert QUOTE_CHUNK[span[0] : span[1]] == "published Hit Refresh in 2017"


def test_rewrapped_quote_still_matches():
    """Line wrapping differs cosmetically; that must not cost a valid claim."""
    from scripto.pipeline.extract import locate_quote

    span = locate_quote("Microsoft in 2014.\n   He published", QUOTE_CHUNK)
    assert span is not None


def test_fabricated_quote_is_rejected():
    from scripto.pipeline.extract import locate_quote

    assert locate_quote("became chief executive of Google in 2014", QUOTE_CHUNK) is None


def test_validated_claim_span_always_supports_the_claim():
    """The regression that mattered: offsets that resolve but point elsewhere.

    Model-reported offsets pointed at unrelated text 30% of the time on real
    output. A located quote cannot do that -- the span is derived from the
    match, so it always contains the quoted text.
    """
    claim = validate_claim(
        {
            "text": "Nadella published Hit Refresh in 2017.",
            "kind": "biographical",
            "quote": "He published Hit Refresh in 2017",
            # A bogus offset pair that would previously have been trusted.
            "verbatim_span": [0, 30],
        },
        QUOTE_CHUNK,
    )
    assert claim is not None
    quoted = QUOTE_CHUNK[claim["span_start"] : claim["span_end"]]
    assert "Hit Refresh" in quoted, f"span points at unrelated text: {quoted!r}"
