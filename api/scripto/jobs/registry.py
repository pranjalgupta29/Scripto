"""Job kind -> handler registry.

Handlers take (db, job) and run inside the worker's transaction. Raising marks
the job failed and schedules a retry; returning normally completes it.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from scripto.models import Job

Handler = Callable[[Session, Job], None]

_HANDLERS: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return decorator


def get_handler(kind: str) -> Handler:
    if kind not in _HANDLERS:
        raise KeyError(f"no handler registered for job kind {kind!r}")
    return _HANDLERS[kind]


def registered_kinds() -> list[str]:
    return sorted(_HANDLERS)


def load_handlers() -> None:
    """Import the pipeline modules so their @register decorators run."""
    from scripto.pipeline import (  # noqa: F401
        cluster,
        coverage,
        discover,
        dossier,
        embed,
        extract,
        fetch,
        identify,
        script,
    )
