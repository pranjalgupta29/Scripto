"""Test fixtures.

Everything runs against a real Postgres with pgvector and the fake providers,
so the tests exercise the actual SQL, the actual job queue and the actual
guards -- not mocks of them.
"""

from __future__ import annotations

import os

# Must be set before scripto.config is imported anywhere.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://scripto:scripto@localhost:5433/scripto_test"
)
os.environ["LLM_PROVIDER"] = "fake"
os.environ["EMBEDDING_PROVIDER"] = "fake"
os.environ["SEARCH_PROVIDER"] = "fake"
os.environ["BLOB_BACKEND"] = "local"
os.environ["BLOB_LOCAL_ROOT"] = "./var/test-blobs"

# Fake providers have no rate limit, so pacing would only make the suite slow.
# Tests that exercise pacing set their own rpm via monkeypatch.
os.environ["PROVIDER_RPM_LLM"] = "0"
os.environ["PROVIDER_RPM_SEARCH"] = "0"
os.environ["PROVIDER_RPM_EMBEDDING"] = "0"
os.environ["PROVIDER_RPM_FETCH"] = "0"

# Pin the quotas too: a low cap set in a developer's .env for cost reasons
# must not silently change what the tests exercise.
os.environ["MAX_SOURCES_PER_EPISODE"] = "25"
os.environ["MAX_EPISODES_PER_USER_PER_DAY"] = "1000"
os.environ["BUDGET_LLM_CALLS_PER_MONTH"] = "-1"
os.environ["BUDGET_SEARCH_CALLS_PER_MONTH"] = "-1"
os.environ["BUDGET_EMBEDDING_CALLS_PER_MONTH"] = "-1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from scripto.db import Base, SessionLocal, engine  # noqa: E402
from scripto.jobs.registry import load_handlers  # noqa: E402
from scripto.main import app  # noqa: E402
from scripto import models  # noqa: E402,F401


@pytest.fixture(scope="session", autouse=True)
def _schema():
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    load_handlers()
    yield


@pytest.fixture(autouse=True)
def _clean():
    """Truncate between tests so each starts from a known state."""
    with engine.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_client(client):
    """A signed-up user's client, with the bearer token applied."""
    response = client.post(
        "/auth/signup", json={"email": "host@example.com", "password": "supersecret1"}
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client
