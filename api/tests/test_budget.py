"""The spend guard must refuse the call, not merely record it."""

from __future__ import annotations

import pytest

from scripto.config import settings
from scripto.jobs.limits import BudgetExceeded, provider_slot, usage


def test_budget_refuses_once_the_cap_is_reached(monkeypatch):
    monkeypatch.setattr(settings, "budget_search_calls_per_month", 3)

    for _ in range(3):
        with provider_slot("search"):
            pass

    used, cap = usage("search")
    assert (used, cap) == (3, 3)

    # The fourth call must be refused before any request is made.
    with pytest.raises(BudgetExceeded, match="exhausted"):
        with provider_slot("search"):
            raise AssertionError("the call should never have been attempted")


def test_zero_budget_disables_a_provider_entirely(monkeypatch):
    monkeypatch.setattr(settings, "budget_search_calls_per_month", 0)

    with pytest.raises(BudgetExceeded, match="disabled"):
        with provider_slot("search"):
            raise AssertionError("a disabled provider must never be called")


def test_negative_budget_means_unlimited(monkeypatch):
    monkeypatch.setattr(settings, "budget_fetch_calls_per_month", -1)

    for _ in range(5):
        with provider_slot("fetch"):
            pass

    used, cap = usage("fetch")
    assert cap == -1
    # Unlimited providers are not metered at all.
    assert used == 0


def test_budget_is_enforced_across_concurrent_workers(monkeypatch):
    """The increment and the check are one statement, so a race cannot overshoot."""
    import threading

    monkeypatch.setattr(settings, "budget_llm_calls_per_month", 5)

    granted: list[int] = []
    refused: list[int] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            with provider_slot("llm", wait_seconds=10):
                with lock:
                    granted.append(1)
        except BudgetExceeded:
            with lock:
                refused.append(1)

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(granted) == 5, f"budget overshot: {len(granted)} calls allowed, cap was 5"
    assert len(refused) == 15


def test_pacing_holds_requests_to_the_rpm_ceiling(monkeypatch):
    """A concurrency cap does not bound request rate; pacing must."""
    import time

    monkeypatch.setattr(settings, "budget_llm_calls_per_month", -1)
    monkeypatch.setattr(settings, "provider_rpm_llm", 120)  # one call / 0.5s

    start = time.monotonic()
    for _ in range(4):
        with provider_slot("llm"):
            pass
    elapsed = time.monotonic() - start

    # 4 calls at 2/s cannot finish faster than ~1.5s of enforced spacing.
    assert elapsed >= 1.2, f"pacing did not throttle: {elapsed:.2f}s for 4 calls"


def test_pacing_is_shared_across_concurrent_workers(monkeypatch):
    import threading
    import time

    monkeypatch.setattr(settings, "budget_llm_calls_per_month", -1)
    monkeypatch.setattr(settings, "provider_rpm_llm", 120)
    monkeypatch.setattr(settings, "provider_cap_llm", 8)

    start = time.monotonic()

    def attempt() -> None:
        with provider_slot("llm", wait_seconds=30):
            pass

    threads = [threading.Thread(target=attempt) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    elapsed = time.monotonic() - start
    # Without shared pacing these would all fire at once and finish instantly.
    assert elapsed >= 2.0, f"parallel workers bypassed pacing: {elapsed:.2f}s for 6 calls"
