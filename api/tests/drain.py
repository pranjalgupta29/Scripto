"""Run the job queue to completion, the same way the worker does.

Uses the real dequeue, the real handlers and the real failure path, so a test
that drains the queue is testing the production pipeline rather than a
reimplementation of it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from scripto.db import session_scope
from scripto.jobs import queue
from scripto.jobs.registry import get_handler
from scripto.models import Job


def drain(max_jobs: int = 500) -> list[Job]:
    """Run queued jobs until none are runnable. Returns the jobs that ran."""
    ran: list[Job] = []

    for _ in range(max_jobs):
        # Delayed jobs would otherwise stall the drain, so pull their schedule
        # forward rather than sleeping through backoff in a test.
        with session_scope() as db:
            db.execute(
                text("UPDATE jobs SET next_attempt_at = now() WHERE state = 'queued'")
            )

        with session_scope() as db:
            job = queue.dequeue(db, worker_id="test")
            if job is None:
                return ran
            job_id, kind = job.id, job.kind

        with session_scope() as db:
            job = db.get(Job, job_id)
            try:
                get_handler(kind)(db, job)
                queue.complete(db, job)
            except Exception as exc:  # mirrors the worker's failure path
                db.rollback()
                job = db.get(Job, job_id)
                queue.fail(db, job, f"{type(exc).__name__}: {exc}")

        with session_scope() as db:
            ran.append(db.get(Job, job_id))

    raise AssertionError(f"queue did not settle within {max_jobs} jobs")
