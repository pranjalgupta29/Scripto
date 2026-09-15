"""Eval runner.

Reports, per fixture:
  * recall on the facts that must surface
  * count of unsourced sentences that reached the output
  * count of trap hits (wrong person, outdated role, claims about someone else)

Run this on every prompt or model change. Without it you cannot tell whether a
change helped, and silent regressions go unnoticed.

Usage:
    python -m evals.runner                 # all fixtures
    python -m evals.runner dana-reyes      # one fixture
    python -m evals.runner --json          # machine readable
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

# Evals default to an isolated database so a run never touches dev data.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://scripto:scripto@localhost:5433/scripto_test"
)
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("EMBEDDING_PROVIDER", "fake")
os.environ.setdefault("SEARCH_PROVIDER", "fake")
os.environ.setdefault("BLOB_LOCAL_ROOT", "./var/eval-blobs")

# api/.env holds limits tuned for real providers (request pacing, source caps,
# budgets). Pin them so they cannot slow or distort an eval run.
for _key, _value in {
    "PROVIDER_RPM_LLM": "0",
    "PROVIDER_RPM_SEARCH": "0",
    "PROVIDER_RPM_EMBEDDING": "0",
    "PROVIDER_RPM_FETCH": "0",
    "MAX_SOURCES_PER_EPISODE": "25",
    "BUDGET_LLM_CALLS_PER_MONTH": "-1",
    "BUDGET_SEARCH_CALLS_PER_MONTH": "-1",
    "BUDGET_EMBEDDING_CALLS_PER_MONTH": "-1",
}.items():
    os.environ.setdefault(_key, _value)

sys.path.insert(0, str(Path(__file__).parent.parent / "api"))

from sqlalchemy import select, text  # noqa: E402

from scripto.blob import checksum, get_blob  # noqa: E402
from scripto.db import Base, engine, session_scope  # noqa: E402
from scripto.jobs import queue  # noqa: E402
from scripto.jobs.registry import get_handler, load_handlers  # noqa: E402
from scripto.models import (  # noqa: E402
    ClaimCluster,
    DossierItem,
    Entity,
    Episode,
    EpisodeSource,
    Job,
    Source,
    Topic,
    User,
)
from scripto.config import settings  # noqa: E402


@dataclass
class Report:
    fixture: str
    facts_total: int = 0
    facts_found: int = 0
    missing_facts: list[str] = field(default_factory=list)
    unsourced_sentences: int = 0
    trap_hits: list[str] = field(default_factory=list)
    coverage_mode: str | None = None
    dossier_items: int = 0
    clusters: int = 0
    repeated_story_found: bool = False
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.facts_found / self.facts_total if self.facts_total else 0.0

    @property
    def passed(self) -> bool:
        return (
            not self.errors
            and self.unsourced_sentences == 0
            and not self.trap_hits
            and self.recall >= 0.6
        )


def _drain(max_jobs: int = 800) -> None:
    """Run the queue to completion; see tests/drain.py for the clock semantics."""
    for _ in range(max_jobs):
        with session_scope() as db:
            job = queue.dequeue(db, worker_id="eval")
            if job is None:
                # Nothing is due. Advance the simulated clock instead of
                # sleeping; stop once nothing is waiting at all.
                waiting = db.execute(
                    text("UPDATE jobs SET next_attempt_at = now() WHERE state = 'queued'")
                ).rowcount
                if not waiting:
                    return
                continue
            job_id, kind = job.id, job.kind
        with session_scope() as db:
            job = db.get(Job, job_id)
            try:
                get_handler(kind)(db, job)
                queue.complete(db, job)
            except queue.Reschedule as exc:
                db.rollback()
                queue.reschedule(db, db.get(Job, job_id), exc.seconds)
            except Exception as exc:
                db.rollback()
                queue.fail(db, db.get(Job, job_id), f"{type(exc).__name__}: {exc}")
    raise RuntimeError("eval queue did not settle")


def run_fixture(spec: dict) -> Report:
    report = Report(fixture=spec["id"])

    # Fresh schema per fixture so runs are independent and comparable.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    load_handlers()

    with session_scope() as db:
        user = User(id=uuid.uuid4(), email=f"eval-{uuid.uuid4()}@x.com", password_hash="x")
        db.add(user)
        db.flush()

        entity = Entity(
            id=uuid.uuid4(),
            type="person",
            name=spec["guest_name"],
            aliases=[],
            external_ids={},
            employer=spec.get("disambiguator"),
        )
        db.add(entity)
        db.flush()

        episode = Episode(
            id=uuid.uuid4(),
            user_id=user.id,
            title=f"Eval: {spec['guest_name']}",
            guest_entity_id=entity.id,
            guest_name=spec["guest_name"],
            disambiguator=spec.get("disambiguator", ""),
            status="ingesting",
        )
        db.add(episode)
        db.flush()

        for i, topic in enumerate(spec.get("topics", ["Career", "Views", "Outlook"])):
            db.add(Topic(id=uuid.uuid4(), episode_id=episode.id, ordinal=i, text=topic))

        # Fixture sources go in as pasted text: deterministic, no network.
        for source_spec in spec["sources"]:
            raw = source_spec["text"].encode()
            digest = checksum(raw)
            source = Source(
                id=uuid.uuid4(),
                type="user_pasted",
                canonical_url=f"eval:{digest}",
                title=source_spec.get("title"),
                blob_ref=get_blob().put(digest, raw),
                checksum=digest,
                status="fetched",
            )
            db.add(source)
            db.flush()
            db.add(
                EpisodeSource(episode_id=episode.id, source_id=source.id, added_by="system")
            )
            queue.enqueue(
                db,
                kind="parse_source",
                episode_id=episode.id,
                user_id=user.id,
                payload={"source_id": str(source.id)},
                idempotency_key=f"parse:{source.id}",
            )

        queue.enqueue(
            db,
            kind="coverage_check",
            episode_id=episode.id,
            user_id=user.id,
            payload={},
        )
        episode_id = episode.id

    try:
        _drain()
    except Exception as exc:
        report.errors.append(str(exc))
        return report

    with session_scope() as db:
        episode = db.get(Episode, episode_id)
        report.coverage_mode = episode.coverage_mode

        items = list(
            db.scalars(select(DossierItem).where(DossierItem.episode_id == episode_id))
        )
        report.dossier_items = len(items)

        # Unsourced sentences: anything that reached the dossier without a
        # resolvable claim id behind it.
        report.unsourced_sentences = sum(
            1 for i in items if not i.claim_ids or i.flagged_unsourced
        )

        haystack = " ".join(i.text for i in items).lower()

        for fact in spec.get("required_facts", []):
            if _fuzzy_contains(haystack, fact.lower()):
                report.facts_found += 1
            else:
                report.missing_facts.append(fact)
        report.facts_total = len(spec.get("required_facts", []))

        for trap in spec.get("traps", []):
            if _fuzzy_contains(haystack, trap.lower()):
                report.trap_hits.append(trap)

        clusters = list(
            db.scalars(
                select(ClaimCluster).where(ClaimCluster.subject_entity_id == episode.guest_entity_id)
            )
        )
        report.clusters = len(clusters)
        report.repeated_story_found = any(
            c.source_count >= settings.already_covered_min_sources for c in clusters
        )

    if spec.get("expect_repeated_story") and not report.repeated_story_found:
        # The fake embedder is hashed bag-of-words: it cannot match paraphrases,
        # so clustering is not meaningfully testable without a semantic provider.
        # Flag it as unevaluated rather than pretend it is a regression.
        if settings.embedding_provider == "fake":
            report.skipped.append(
                "repeated-story clustering needs a semantic embedding provider "
                "(EMBEDDING_PROVIDER=fake cannot match paraphrases)"
            )
        else:
            report.errors.append(
                "expected a repeated story cluster spanning >=3 sources, found none"
            )

    expected_modes = spec.get("expect_coverage_mode")
    if expected_modes and report.coverage_mode not in expected_modes:
        report.errors.append(
            f"coverage mode {report.coverage_mode!r} not in expected {expected_modes}"
        )

    return report


def _fuzzy_contains(haystack: str, needle: str) -> bool:
    """Match on content words so wording differences do not count as misses."""
    if needle in haystack:
        return True
    words = [w for w in needle.split() if len(w) > 3]
    if not words:
        return False
    hits = sum(1 for w in words if w in haystack)
    return hits / len(words) >= 0.75


def main() -> int:
    parser = argparse.ArgumentParser(description="Scripto eval harness")
    parser.add_argument("fixtures", nargs="*", help="fixture ids (default: all)")
    parser.add_argument("--json", action="store_true", help="machine readable output")
    args = parser.parse_args()

    paths = sorted(FIXTURES.glob("*.json"))
    if args.fixtures:
        paths = [p for p in paths if p.stem in args.fixtures]
    if not paths:
        print("no fixtures found", file=sys.stderr)
        return 2

    reports = [run_fixture(json.loads(p.read_text())) for p in paths]

    if args.json:
        print(
            json.dumps(
                [{**r.__dict__, "recall": r.recall, "passed": r.passed} for r in reports], indent=2
            )
        )
    else:
        print(f"\n{'fixture':<20} {'recall':>8} {'unsourced':>10} {'traps':>6} {'mode':>7}  result")
        print("-" * 72)
        for r in reports:
            status = "PASS" if r.passed else "FAIL"
            print(
                f"{r.fixture:<20} {r.recall:>7.0%} {r.unsourced_sentences:>10} "
                f"{len(r.trap_hits):>6} {str(r.coverage_mode):>7}  {status}"
            )
            for fact in r.missing_facts:
                print(f"    missing fact: {fact}")
            for trap in r.trap_hits:
                print(f"    TRAP HIT: {trap}")
            for err in r.errors:
                print(f"    error: {err}")
            for skip in r.skipped:
                print(f"    not evaluated: {skip}")
        print()

    return 0 if all(r.passed for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
