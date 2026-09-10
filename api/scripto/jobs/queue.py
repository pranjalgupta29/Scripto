"""Postgres-backed job queue.

Dequeue is `SELECT ... FOR UPDATE SKIP LOCKED` with a lease. Ordering is
deliberately not FIFO: one user starting a 25 source episode must not starve
everyone else, so we sort by that user's currently-running job count first and
only then by created_at.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from scripto.config import settings
from scripto.models import Job

# Fair dequeue. COALESCE handles users with no running jobs, who sort first.
_DEQUEUE_SQL = text(
    """
    WITH running AS (
        SELECT user_id, count(*) AS n
        FROM jobs
        WHERE state = 'running'
        GROUP BY user_id
    )
    SELECT j.id
    FROM jobs j
    LEFT JOIN running r ON r.user_id = j.user_id
    WHERE j.state = 'queued'
      AND j.next_attempt_at <= clock_timestamp()
      AND (:kinds_filter = FALSE OR j.kind = ANY(:kinds))
    ORDER BY COALESCE(r.n, 0) ASC, j.created_at ASC
    FOR UPDATE OF j SKIP LOCKED
    LIMIT 1
    """
)


def enqueue(
    db: Session,
    *,
    kind: str,
    episode_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    payload: dict | None = None,
    idempotency_key: str | None = None,
    delay_seconds: float = 0,
) -> Job | None:
    """Insert a job. Returns None if an identical job already exists.

    `idempotency_key` is what makes retries safe: re-running a stage that fans
    out to per-source jobs will not duplicate them.
    """
    values = {
        "id": uuid.uuid4(),
        "kind": kind,
        "episode_id": episode_id,
        "user_id": user_id,
        "payload": payload or {},
        "state": "queued",
        "attempts": 0,
        "next_attempt_at": datetime.now(timezone.utc) + timedelta(seconds=delay_seconds),
        "idempotency_key": idempotency_key,
    }

    if idempotency_key is None:
        job = Job(**values)
        db.add(job)
        db.flush()
        return job

    stmt = (
        pg_insert(Job)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(Job.id)
    )
    row = db.execute(stmt).first()
    db.flush()
    if row is None:
        return None
    return db.get(Job, row[0])


def dequeue(db: Session, *, worker_id: str, kinds: list[str] | None = None) -> Job | None:
    """Claim one job and set its lease. Caller owns the transaction."""
    row = db.execute(
        _DEQUEUE_SQL,
        {"kinds_filter": bool(kinds), "kinds": kinds or []},
    ).first()
    if row is None:
        return None

    job = db.get(Job, row[0])
    if job is None:
        return None

    now = datetime.now(timezone.utc)
    job.state = "running"
    job.attempts += 1
    job.locked_at = now
    job.locked_by = worker_id
    job.lease_expires_at = now + timedelta(seconds=settings.job_lease_seconds)
    job.updated_at = now
    db.flush()
    return job


def heartbeat(db: Session, job_id: uuid.UUID, *, worker_id: str) -> None:
    """Extend the lease while the job is still running."""
    db.execute(
        text(
            """
            UPDATE jobs
            SET lease_expires_at = clock_timestamp() + make_interval(secs => :lease),
                updated_at = clock_timestamp()
            WHERE id = :id AND locked_by = :worker AND state = 'running'
            """
        ),
        {"id": job_id, "worker": worker_id, "lease": settings.job_lease_seconds},
    )


def complete(db: Session, job: Job) -> None:
    job.state = "done"
    job.error = None
    job.locked_by = None
    job.lease_expires_at = None
    job.updated_at = datetime.now(timezone.utc)
    db.flush()


def fail(db: Session, job: Job, error: str) -> None:
    """Exponential backoff, then `dead` rather than looping forever."""
    job.error = error[:4000]
    job.locked_by = None
    job.lease_expires_at = None
    job.updated_at = datetime.now(timezone.utc)

    if job.attempts >= settings.job_max_attempts:
        job.state = "dead"
    else:
        job.state = "queued"
        backoff = min(2 ** job.attempts, 300)
        job.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=backoff)
    db.flush()


def sweep_expired_leases(db: Session) -> int:
    """Requeue jobs whose lease expired. This is how a crashed worker's jobs come back."""
    result = db.execute(
        text(
            """
            UPDATE jobs
            SET state = CASE WHEN attempts >= :max_attempts THEN 'dead' ELSE 'queued' END,
                locked_by = NULL,
                lease_expires_at = NULL,
                error = COALESCE(error, 'lease expired'),
                updated_at = now()
            WHERE state = 'running' AND lease_expires_at < clock_timestamp()
            """
        ),
        {"max_attempts": settings.job_max_attempts},
    )
    return result.rowcount or 0


def episode_progress(db: Session, episode_id: uuid.UUID) -> dict:
    """Job counts by state and kind, for the polling endpoint."""
    rows = db.execute(
        text(
            """
            SELECT kind, state, count(*) AS n
            FROM jobs WHERE episode_id = :eid
            GROUP BY kind, state
            """
        ),
        {"eid": episode_id},
    ).all()

    by_state: dict[str, int] = {}
    by_kind: dict[str, dict[str, int]] = {}
    for kind, state, n in rows:
        by_state[state] = by_state.get(state, 0) + n
        by_kind.setdefault(kind, {})[state] = n

    total = sum(by_state.values())
    finished = by_state.get("done", 0) + by_state.get("dead", 0)
    return {
        "total": total,
        "finished": finished,
        "pending": total - finished,
        "by_state": by_state,
        "by_kind": by_kind,
    }
