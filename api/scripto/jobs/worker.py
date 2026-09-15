"""Worker loop. Runs in its own process; the API never runs pipeline work.

Workers are stateless and horizontally scalable -- scaling out is adding
replicas. Each worker runs N threads, and one thread periodically sweeps
expired leases so a crashed worker's jobs come back.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid

from scripto.config import settings
from scripto.db import session_scope
from scripto.jobs import queue
from scripto.jobs.limits import BudgetExceeded, SlotUnavailable
from scripto.jobs.registry import get_handler, load_handlers
from scripto.models import Job

log = logging.getLogger("scripto.worker")

_shutdown = threading.Event()


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


def _run_one(worker_id: str) -> bool:
    """Claim and run a single job. Returns False when the queue is empty.

    The claim commits before the handler runs, so the lease is durable even if
    the handler crashes the process.
    """
    with session_scope() as db:
        job = queue.dequeue(db, worker_id=worker_id)
        if job is None:
            return False
        job_id, kind = job.id, job.kind

    stop_heartbeat = threading.Event()

    def _beat() -> None:
        while not stop_heartbeat.wait(settings.job_lease_seconds / 3):
            try:
                with session_scope() as hb:
                    queue.heartbeat(hb, job_id, worker_id=worker_id)
            except Exception:  # a failed heartbeat must not kill the job
                log.warning("heartbeat failed for job %s", job_id, exc_info=True)

    beater = threading.Thread(target=_beat, daemon=True)
    beater.start()

    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return True
            handler = get_handler(kind)
            handler(db, job)
            queue.complete(db, job)
        log.info("job %s (%s) done", job_id, kind)
    except queue.Reschedule as exc:
        # Waiting on other work: run this same job again later. No attempt
        # burned, no error recorded, no new row.
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                queue.reschedule(db, job, exc.seconds)
    except BudgetExceeded as exc:
        # Terminal. Retrying cannot make budget appear, and a retry loop is the
        # exact failure mode the budget exists to prevent.
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                job.state = "dead"
                job.error = str(exc)
                job.locked_by = None
                job.lease_expires_at = None
        log.error("job %s stopped: %s", job_id, exc)
    except SlotUnavailable as exc:
        # Not a real failure: the provider is saturated. Requeue without
        # burning an attempt so a busy provider never marks jobs dead.
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                job.attempts = max(0, job.attempts - 1)
                queue.fail(db, job, f"provider busy: {exc}")
        log.info("job %s deferred: %s", job_id, exc)
    except Exception as exc:
        log.exception("job %s (%s) failed", job_id, kind)
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                queue.fail(db, job, f"{type(exc).__name__}: {exc}")
    finally:
        stop_heartbeat.set()

    return True


def _loop(worker_id: str) -> None:
    while not _shutdown.is_set():
        try:
            did_work = _run_one(worker_id)
        except Exception:
            log.exception("worker loop error")
            did_work = False
        if not did_work:
            _shutdown.wait(settings.worker_poll_interval_seconds)


def _sweeper() -> None:
    while not _shutdown.wait(settings.job_lease_seconds / 2):
        try:
            with session_scope() as db:
                n = queue.sweep_expired_leases(db)
            if n:
                log.warning("requeued %d job(s) with expired leases", n)
        except Exception:
            log.exception("sweeper error")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    load_handlers()

    def _stop(signum, frame):  # noqa: ANN001
        log.info("shutdown signal received, finishing in-flight work")
        _shutdown.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    threading.Thread(target=_sweeper, daemon=True).start()

    threads = [
        threading.Thread(target=_loop, args=(f"{_worker_id()}:{i}",), daemon=True)
        for i in range(settings.worker_concurrency)
    ]
    for t in threads:
        t.start()
    log.info("worker up with %d threads", len(threads))

    while not _shutdown.is_set():
        time.sleep(0.5)
    for t in threads:
        t.join(timeout=30)


if __name__ == "__main__":
    main()
