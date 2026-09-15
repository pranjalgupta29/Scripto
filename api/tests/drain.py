"""Run the job queue to completion, the same way the worker does.

Uses the real dequeue, the real handlers and the real failure path, so a test
that drains the queue is testing the production pipeline rather than a
reimplementation of it.

Time is simulated. Jobs run in queue order, and only when nothing is due does
the clock "advance": every waiting job's next_attempt_at is pulled to now.
Pulling everything forward on every step would let a job that reschedules
itself -- the coverage check waiting on ingestion -- jump the queue forever.
"""

from __future__ import annotations

from sqlalchemy import text

from scripto.db import session_scope
from scripto.jobs import queue
from scripto.jobs.registry import get_handler
from scripto.models import Job


def drain(max_jobs: int = 500) -> list[Job]:
    """Run queued jobs until none remain. Returns the jobs that ran."""
    ran: list[Job] = []

    for _ in range(max_jobs):
        with session_scope() as db:
            job = queue.dequeue(db, worker_id="test")
            if job is None:
                # Nothing is due. Advance the simulated clock instead of
                # sleeping; stop once nothing is waiting at all.
                waiting = db.execute(
                    text("UPDATE jobs SET next_attempt_at = now() WHERE state = 'queued'")
                ).rowcount
                if not waiting:
                    return ran
                continue
            job_id, kind = job.id, job.kind

        with session_scope() as db:
            job = db.get(Job, job_id)
            try:
                get_handler(kind)(db, job)
                queue.complete(db, job)
            except queue.Reschedule as exc:  # mirrors the worker
                db.rollback()
                queue.reschedule(db, db.get(Job, job_id), exc.seconds)
            except Exception as exc:  # mirrors the worker's failure path
                db.rollback()
                job = db.get(Job, job_id)
                queue.fail(db, job, f"{type(exc).__name__}: {exc}")

        with session_scope() as db:
            ran.append(db.get(Job, job_id))

    raise AssertionError(f"queue did not settle within {max_jobs} jobs")
