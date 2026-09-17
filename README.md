# Scripto

Podcast guest research and interview scripts. Every line in the dossier and the
script links back to a source you can open.

Built to `build-spec-v1.md`. Scope is one episode, one guest, one script.

For the architecture, the reasoning behind each technology choice, what every
stage does, and the known gaps, see [`docs/system-design.md`](docs/system-design.md).

---

## Stack

| Layer | Choice |
|---|---|
| API | Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic |
| Database | Postgres 16 + pgvector |
| Queue | Postgres jobs table, `SELECT FOR UPDATE SKIP LOCKED`, leases + fair dequeue |
| Blob storage | Local filesystem behind a `Blob` interface, S3 swappable |
| Frontend | Next.js App Router, React, Tailwind, TanStack Query, 2s polling |
| LLM | Anthropic (official SDK) or any OpenAI-compatible endpoint (Gemini in the POC), per-stage models |
| Embeddings | Gemini or Voyage, swappable (fake in the POC) |
| Search | DuckDuckGo (no key), Exa or Tavily, swappable |

Every provider has a deterministic `fake` implementation, so the entire product
runs and is testable with **no API keys and no network**.

---

## Running it

Setting this up on a new machine — Postgres, pgvector, the database, the `.env`
files, and what to do when it won't start — is in
[`docs/setup.md`](docs/setup.md). **No API keys are needed:** every provider has
a fake implementation and the defaults use them.

Postgres 16 with pgvector must be reachable at `DATABASE_URL`. On this machine it
runs via Homebrew on **port 5433** (5432 is occupied by an existing EDB install):

```bash
brew services start postgresql@16
```

### API + worker

```bash
cd api
uv venv --python 3.12 && uv pip install -e ".[dev]"
cp .env.example .env            # fake providers by default, no keys needed
.venv/bin/alembic upgrade head

.venv/bin/uvicorn scripto.main:app --reload --port 8000   # API
.venv/bin/python -m scripto.jobs.worker                   # worker (separate shell)
```

The API process never runs pipeline work. Scaling out means adding worker
replicas.

### Web

```bash
cd web
npm install
npm run dev        # http://localhost:3000
```

### Tests and evals

```bash
cd api && .venv/bin/pytest          # 105 tests
python -m evals.runner              # from the repo root
```

---

## Going live with real providers

Set these in `api/.env` — nothing else changes:

```
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
EMBEDDING_PROVIDER=voyage
EMBEDDING_API_KEY=...
SEARCH_PROVIDER=exa          # or tavily, or duckduckgo (no key needed)
SEARCH_API_KEY=...
```

Models are configured per pipeline stage — a cheap model for extraction, a strong
one for composition (`LLM_EXTRACT_MODEL`, `LLM_COMPOSE_MODEL`, `LLM_RANK_MODEL`).

The current proof of concept runs on Gemini's free tier through the
OpenAI-compatible provider, with DuckDuckGo search. The exact settings and the
reasons for them are in the design doc (§3.8, §13).

---

## How it works

```
identify → discover → fetch → parse → chunk → embed → extract_claims
                                                          ↓
        script ← build_dossier ← coverage_check ← cluster_claims
```

Each stage is a job kind. Jobs are idempotent, keyed on content checksum, and
retried with exponential backoff before moving to `dead`.

### The things that carry the product

**Sources are global.** Deduped on canonical URL and on content checksum. Two
users researching the same guest never refetch or reparse the same page.

**Claims are scoped to the entity, not the episode.** A second episode with the
same guest reuses every claim already extracted. That is most of the cost saved.

**Fabricated claims are dropped in code, not in the prompt.** The model must
quote its supporting text; the quote is located in the chunk by string match
and the claim is discarded if it cannot be found (`pipeline/extract.py`). A
model told not to fabricate will still fabricate.

**Unsourced sentences never reach the UI.** The dossier composer must return the
claim ids each sentence rests on; a verification pass drops any item whose ids
don't resolve to real claims for that subject (`pipeline/dossier.py`).

**Raw payloads are stored before parsing.** Reparsing an existing episode needs
no network fetches — enforced by a test that makes any HTTP call a hard failure.

**Thin footprints are the common case, not the edge case.** Coverage is scored
into `rich | thin | sparse`. Thin and sparse episodes say plainly what was found,
what was missing, and what to paste in. The topic brief is designed to reuse the
same pipeline against a `topic` entity; that wiring is not finished yet (design
doc §10).

**A failed source never fails the episode.** It is marked failed, surfaced in the
UI, and the run continues.

### Concurrency

- Fair dequeue: ordered by the user's currently-running job count, then
  `created_at`. Pure FIFO would let one 25-source episode starve everyone else.
- Leases with heartbeats; a sweeper requeues jobs from crashed workers.
- Provider-wide concurrency caps shared across all workers via Postgres advisory
  locks (`jobs/limits.py`) — external rate limits are the real bottleneck, not
  Postgres.
- Per-user daily episode quotas and per-episode source caps from day one.

---

## Deferred, with seams left in place

- **Whisper transcription.** `adapters/youtube.py` walks an ordered list of
  `TranscriptStrategy` objects. `WhisperStrategy` is present and returns
  `available() == False`. Implementing it means downloading audio and returning
  the same `Segment` list — chunking, extraction and claims are untouched.
- LinkedIn vendor adapter behind a feature flag.
- Team accounts and sharing.

---

## Layout

```
api/
  scripto/
    adapters/    source adapters, one per type
    pipeline/    identify, discover, fetch, chunking, embed, extract,
                 cluster, coverage, dossier, script
    llm/         provider interface, prompts, structured output
    jobs/        queue, worker loop, registry, provider caps
    routes/      auth, episodes, export
    models.py    full schema
  tests/         105 tests incl. the section 9 acceptance criteria
web/             next.js app
evals/           fixtures + runner
```
