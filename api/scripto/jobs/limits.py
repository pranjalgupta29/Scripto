"""Provider-wide concurrency caps, shared across all worker processes.

External rate limits are the real bottleneck, not Postgres. A per-process
semaphore is useless once workers scale out, so the cap lives in the database:
each provider owns N advisory-lock slots and a worker must take one before
calling out. Tune the caps from observed 429s.
"""

from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import text

from scripto.config import settings
from scripto.db import engine


class SlotUnavailable(RuntimeError):
    """Every slot for a provider is taken. The job should be retried later."""


class BudgetExceeded(RuntimeError):
    """The monthly call ceiling for a provider is spent.

    Terminal, not retryable: retrying cannot make budget appear, and a retry
    loop against a paid provider is exactly what the budget exists to prevent.
    """


def _budget(provider: str) -> int:
    return {
        "llm": settings.budget_llm_calls_per_month,
        "search": settings.budget_search_calls_per_month,
        "embedding": settings.budget_embedding_calls_per_month,
        "fetch": settings.budget_fetch_calls_per_month,
    }.get(provider, -1)


def _charge(provider: str) -> None:
    """Reserve one call against the monthly budget, or refuse.

    The increment and the check are a single atomic statement, so concurrent
    workers cannot race past the ceiling together.
    """
    cap = _budget(provider)
    if cap < 0:
        return
    if cap == 0:
        raise BudgetExceeded(f"provider {provider!r} is disabled (budget 0)")

    period = datetime.now(timezone.utc).strftime("%Y-%m")
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO provider_budgets (id, provider, period, count, updated_at)
                VALUES (gen_random_uuid(), :provider, :period, 1, now())
                ON CONFLICT (provider, period) DO UPDATE
                    SET count = provider_budgets.count + 1, updated_at = now()
                    WHERE provider_budgets.count < :cap
                RETURNING count
                """
            ),
            {"provider": provider, "period": period, "cap": cap},
        ).first()

    if row is None:
        raise BudgetExceeded(
            f"monthly budget for {provider!r} exhausted ({cap} calls). "
            f"Raise BUDGET_{provider.upper()}_CALLS_PER_MONTH to continue."
        )


def usage(provider: str) -> tuple[int, int]:
    """(calls used this month, cap). Cap of -1 means unlimited."""
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    with engine.connect() as conn:
        used = conn.execute(
            text(
                "SELECT count FROM provider_budgets WHERE provider = :p AND period = :period"
            ),
            {"p": provider, "period": period},
        ).scalar()
    return int(used or 0), _budget(provider)


def _rpm(provider: str) -> int:
    return {
        "llm": settings.provider_rpm_llm,
        "search": settings.provider_rpm_search,
        "embedding": settings.provider_rpm_embedding,
        "fetch": settings.provider_rpm_fetch,
    }.get(provider, 0)


def _pace(provider: str) -> None:
    """Block until this provider's next call is allowed under its rpm ceiling.

    The claim and the wait are split: we reserve the next slot inside a single
    atomic UPDATE (so concurrent workers each get a distinct one), then sleep
    outside the transaction rather than holding a row lock while waiting.
    """
    rpm = _rpm(provider)
    if rpm <= 0:
        return

    interval = 60.0 / rpm
    with engine.begin() as conn:
        wait = conn.execute(
            text(
                """
                INSERT INTO provider_pacing (provider, last_call_at)
                VALUES (:p, clock_timestamp())
                ON CONFLICT (provider) DO UPDATE
                    SET last_call_at = GREATEST(
                        provider_pacing.last_call_at + make_interval(secs => :interval),
                        clock_timestamp()
                    )
                RETURNING EXTRACT(EPOCH FROM (last_call_at - clock_timestamp()))
                """
            ),
            {"p": provider, "interval": interval},
        ).scalar()

    if wait and wait > 0:
        time.sleep(min(float(wait), 120.0))


def _cap(provider: str) -> int:
    return {
        "llm": settings.provider_cap_llm,
        "search": settings.provider_cap_search,
        "fetch": settings.provider_cap_fetch,
        "embedding": settings.provider_cap_embedding,
    }.get(provider, 4)


def _key(provider: str, slot: int) -> int:
    digest = hashlib.sha256(f"{provider}:{slot}".encode()).digest()
    # Postgres advisory locks take a signed 64-bit key.
    return int.from_bytes(digest[:8], "big", signed=True)


@contextmanager
def provider_slot(provider: str, *, wait_seconds: float = 30.0, poll: float = 0.25):
    """Hold one of the provider's concurrency slots for the duration of the block.

    Uses a dedicated connection because advisory locks are session scoped: a
    pooled connection returned mid-call would drop the lock.
    """
    # Budget first: never queue for a slot you are not allowed to spend.
    _charge(provider)
    # Then pace, so the request rate stays under the provider's ceiling.
    _pace(provider)

    cap = _cap(provider)
    deadline = time.monotonic() + wait_seconds
    conn = engine.connect()
    acquired: int | None = None
    try:
        while acquired is None:
            for slot in range(cap):
                got = conn.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": _key(provider, slot)}
                ).scalar()
                if got:
                    acquired = slot
                    break
            if acquired is None:
                if time.monotonic() >= deadline:
                    raise SlotUnavailable(f"no free slot for provider {provider!r} (cap {cap})")
                time.sleep(poll)
        yield acquired
    finally:
        if acquired is not None:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _key(provider, acquired)})
        conn.close()
