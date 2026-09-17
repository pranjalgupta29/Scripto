"""FastAPI app. This process never runs pipeline work -- that is the worker's job."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from scripto.config import settings
from scripto.routes import auth, episodes, export, prep

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Scripto API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(episodes.router)
app.include_router(export.router)
app.include_router(prep.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
        "search_provider": settings.search_provider,
    }


@app.get("/usage", tags=["meta"])
def provider_usage() -> dict:
    """This month's billable calls against each provider's hard ceiling."""
    from scripto.jobs.limits import usage

    out = {}
    for provider in ("llm", "search", "embedding", "fetch"):
        used, cap = usage(provider)
        out[provider] = {
            "used": used,
            "cap": "unlimited" if cap < 0 else cap,
            "remaining": "unlimited" if cap < 0 else max(0, cap - used),
        }
    return out
