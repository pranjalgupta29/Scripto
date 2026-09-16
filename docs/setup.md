# Setting Scripto up on a new machine

**You do not need any API keys to run Scripto.** Every provider — LLM, embeddings,
web search — has a `fake` implementation, and the defaults use them. The app runs
end to end with no keys and no network. Keys only buy you real research (§6).

What you do need is Postgres with pgvector, and an `api/.env` file. A missing
`api/.env` is the most common reason the API won't start: `DATABASE_URL` has no
default, so the process exits immediately.

---

## 1. Prerequisites

| Need | Version | Notes |
|---|---|---|
| Postgres | 16 | with the **pgvector** extension available |
| Python | 3.12 | `uv` fetches it; the repo pins `>=3.12,<3.13` |
| Node | 20 | with npm |

macOS with Homebrew:

```bash
brew install postgresql@16 uv node
brew services start postgresql@16
```

**pgvector.** The first migration runs `CREATE EXTENSION IF NOT EXISTS vector`,
which only works if pgvector is installed for *this* Postgres. Homebrew's
`pgvector` bottle targets Postgres 17/18, so on Postgres 16 it has to be built
from source:

```bash
git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git /tmp/pgvector
cd /tmp/pgvector && make && make install    # uses pg_config from your PATH
```

If `pg_config` points at the wrong Postgres, prefix it:
`PG_CONFIG=/opt/homebrew/opt/postgresql@16/bin/pg_config make install`.

## 2. Database

Two databases: one for development, one for tests and evals.

```bash
createuser -s scripto                 # -s so it can CREATE EXTENSION
psql -d postgres -c "ALTER ROLE scripto PASSWORD 'scripto'"
createdb -O scripto scripto
createdb -O scripto scripto_test
```

Add `-p <port>` to each command if your Postgres is not on the default port.

## 3. API and worker

```bash
cd api
uv venv --python 3.12 && uv pip install -e ".[dev]"
cp .env.example .env
.venv/bin/alembic upgrade head

.venv/bin/uvicorn scripto.main:app --reload --port 8000    # API
.venv/bin/python -m scripto.jobs.worker                    # worker, separate shell
```

**Check `DATABASE_URL` in `api/.env` before running the migration.** The example
file points at port **5433**, because the machine this was built on already had
another Postgres on 5432. On a normal install you want 5432:

```
DATABASE_URL=postgresql+psycopg://scripto:scripto@localhost:5432/scripto
```

The API process never runs pipeline work, so the worker has to be running too or
nothing past "created" ever happens to an episode.

## 4. Web

```bash
cd web
npm install
npm run dev        # http://localhost:3000
```

`web/.env.local` is optional: the API base URL falls back to
`http://localhost:8000`. Copy `web/.env.example` to `web/.env.local` only if your
API runs somewhere else.

## 5. First run

1. Open http://localhost:3000 and **create an account**. It is local to your
   database; any email and password will do.
2. Create an episode, confirm the guest, add topics, and wait for research.
3. With the default fake providers the sources and dossier are synthetic but the
   whole pipeline is real, including the citation checks.

```bash
cd api && .venv/bin/pytest          # 90 tests, no keys, no network
```

## 6. Real providers (optional)

Everything real is configured in `api/.env`; no code changes. **`api/.env` is
gitignored — keep it that way, and never paste a key into a chat, a commit or a
shared doc. Each person uses their own key.**

The proof of concept runs on Gemini's free tier through the OpenAI-compatible
endpoint, with DuckDuckGo search, which needs no key and no card:

```
LLM_PROVIDER=openai_compatible
OPENAI_API_KEY=<your own Gemini key from aistudio.google.com>
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_EXTRACT_MODEL=gemini-3.1-flash-lite
LLM_COMPOSE_MODEL=gemini-3.1-flash-lite
LLM_RANK_MODEL=gemini-3.1-flash-lite

SEARCH_PROVIDER=duckduckgo
EMBEDDING_PROVIDER=fake

# free tier: pace requests or you get 429s
PROVIDER_RPM_LLM=12
PROVIDER_RPM_SEARCH=20
PROVIDER_RPM_EMBEDDING=12
PROVIDER_CAP_LLM=2
PROVIDER_CAP_EMBEDDING=2
MAX_SOURCES_PER_EPISODE=8

# hard ceilings, enforced in code before any billable call
BUDGET_LLM_CALLS_PER_MONTH=2000
BUDGET_SEARCH_CALLS_PER_MONTH=300
BUDGET_EMBEDDING_CALLS_PER_MONTH=500
```

Anthropic and Voyage work the same way — see the README's "Going live with real
providers" and §14 of the design doc for every setting.

Budgets are counted per calendar month in the `provider_budgets` table. Setting a
budget to `0` disables that provider outright; `-1` is unlimited. A run that hits
a budget stops rather than spending.

---

## Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| `ValidationError: database_url Field required` on startup | no `api/.env` | `cp api/.env.example api/.env` |
| `connection refused ... 5433` | Postgres isn't running, or is on another port | start it, or fix `DATABASE_URL` |
| `FATAL: role "scripto" does not exist` | role never created | §2 |
| `FATAL: database "scripto_test" does not exist` | tests need their own database | §2 |
| `ERROR: extension "vector" is not available` | pgvector isn't installed for this Postgres | §1 |
| `permission denied to create extension "vector"` | the role isn't a superuser | `psql -d postgres -c "ALTER ROLE scripto SUPERUSER"` |
| Episodes stay in the first stage forever | the worker isn't running | §3 |
| Login works, everything else 401s | token expired; sign in again | — |
| Results look canned | fake providers, which is the default | §6 |
| `429` from the LLM | free-tier rate limit | lower `PROVIDER_RPM_LLM` |
| A stage stops with a budget error | the monthly ceiling was reached | raise it in `api/.env`, or wait for next month |
