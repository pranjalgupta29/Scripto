# Podcast Research Platform, v1 Build Spec

Scope: one episode, one guest, one script. Everything else is out.

Reference user: marketing head at a 25 person startup, preparing an episode with an
invited financial analyst.

---

## 1. Non-goals for v1

Write these down so the build does not drift.

- No LinkedIn or Instagram scraping. Profile data comes from the user pasting a URL or
  text, or from a vendor adapter behind a feature flag.
- No audio transcription. YouTube captions only. Whisper comes in v1.5.
- No team collaboration, comments, or sharing. Single user per workspace.
- No publishing, clips, show notes, or anything post-recording.
- No topic ideation. The user supplies the topics. Topic and industry *research* is in
  scope, suggesting what to talk about is not.
- No billing.

### Deferred, keep the seams for them

- **Whisper transcription.** Many prior podcast appearances have no captions, and those
  are the single best source for this product. Build the `youtube` adapter so that
  transcript retrieval is a swappable step: try captions first, and leave a clean hook
  where an audio download plus Whisper call slots in later without touching the
  chunking or claims code.
- Team accounts and sharing.
- LinkedIn vendor adapter behind a feature flag.

---

## 2. Decisions already made

Override any of these if you disagree, but do not leave them open.

| Area | Decision |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic |
| Database | Postgres 16 with pgvector |
| Queue | Postgres backed jobs table using SELECT FOR UPDATE SKIP LOCKED. No Redis, no Celery |
| Blob storage | Local filesystem in dev behind a Blob interface, S3 compatible later |
| Frontend | Next.js App Router, React, Tailwind, TanStack Query |
| Progress updates | Client polls job status every 2s. No websockets |
| LLM | Provider configurable per pipeline stage via env vars, OpenAI compatible interface so DeepSeek, Anthropic and others are drop in. Cheap model for extraction, strong model for composition |
| Hosting | Runs locally via Docker Compose, but no local-only shortcuts. Every path, secret and service address comes from env vars so the same image deploys unchanged |
| Embeddings | Configurable provider, start with Voyage |
| Web discovery | A search API (Exa or Tavily) for finding sources about the guest |
| Auth | Email plus password, single workspace per user. Keep it boring |

---

## 3. User journey

1. User creates an episode and enters guest name plus one disambiguator (LinkedIn URL,
   firm name, or X handle). Name alone is rejected.
2. System returns 2 to 5 candidate identities with photo, current role, employer.
   User picks one. Wrong identity poisons everything downstream, so this step is not
   skippable.
3. Ingestion job runs. Sources appear on the page as they land. User can paste
   additional URLs or remove irrelevant ones at any point.
4. Dossier renders: career timeline, recent news, public positions, and a
   "already covered" section of things the guest has said repeatedly elsewhere.
   Every item links back to its source.
5. User enters 3 to 6 topics they want the episode to cover.
6. User picks a style preset, optionally uploads a past episode transcript to match voice.
7. Script generates: segments, questions, follow-ups, risk flags, each tied to sources.
8. User edits inline and exports to PDF or Google Doc.

---

## 4. Data model

```
users(id, email, password_hash, created_at)

entities(id, type, name, aliases jsonb, external_ids jsonb, created_at)
  -- type: person | company | topic
  -- topic entities let the same pipeline research an industry or subject
  -- with no new machinery, only a different composer

episodes(id, user_id, title, guest_entity_id, status, created_at)
  -- status: identifying | ingesting | dossier_ready | script_ready

sources(id, type, url, canonical_url, title, author,
        published_at, fetched_at, blob_ref, checksum, status, error)
  -- type: web_article | youtube | pdf | profile | user_pasted
  -- status: pending | fetched | parsed | failed
  -- GLOBAL, not per episode. unique on canonical_url, and on checksum
  -- two users researching the same guest must never refetch or reparse the same page

episode_sources(episode_id, source_id, added_by, removed_at)
  -- join table. removing a source is per episode, it never deletes the source

chunks(id, source_id, ordinal, text, start_offset, end_offset,
       start_ms, end_ms, speaker, embedding vector(1024))
  -- offsets for text sources, ms for AV sources. Exactly one pair is populated

claims(id, chunk_id, subject_entity_id, text, kind,
       claim_date, cluster_id, extractor_version, created_at)
  -- kind: biographical | opinion | fact | anecdote | prediction
  -- scoped to the ENTITY, not the episode. A second episode with the same guest
  -- reuses every claim already extracted, which is most of the cost saved

claim_clusters(id, subject_entity_id, canonical_text, source_count, first_seen, last_seen)
  -- source_count is what powers "he has said this in 9 places"

topics(id, episode_id, ordinal, text)

scripts(id, episode_id, style_preset, voice_sample_ref, model_version, created_at)

script_segments(id, script_id, ordinal, topic_id, question, rationale,
                expected_direction, followups jsonb, risk_flags jsonb)

citations(id, target_type, target_id, chunk_id, span_start, span_end)
  -- target_type: dossier_item | script_segment

jobs(id, episode_id, user_id, kind, payload jsonb, state, attempts,
     locked_at, locked_by, lease_expires_at, next_attempt_at, error,
     created_at, updated_at)
  -- kind: identify | discover | fetch_source | parse_source | embed |
  --       extract_claims | cluster_claims | build_dossier | generate_script
  -- state: queued | running | done | failed | dead
  -- user_id is on the job so the dequeue can be fair across users
```

---

## 4b. Concurrency and scale

The workload is bursty, long running and IO bound. It is not high QPS. So scale here means
surviving many users starting expensive multi minute jobs at once, not requests per second.

**Process separation.** The API process never runs pipeline work. Workers are separate
containers, stateless, horizontally scalable. Scaling out is adding worker replicas.

**Dequeue.** `SELECT ... FOR UPDATE SKIP LOCKED` with a lease. A worker sets
`lease_expires_at = now() + interval '10 minutes'` and heartbeats it while running. A sweeper
requeues anything whose lease has expired, which is how a crashed worker's jobs come back.
Retries use `next_attempt_at` with exponential backoff, and jobs move to `dead` after N
attempts rather than looping forever.

**Fairness.** Do not dequeue in pure FIFO. One user starting a 25 source episode must not
starve everyone else. Order the dequeue by the count of that user's currently running jobs
first, then by created_at. One line of SQL, and it is the difference between a usable and an
unusable system under load.

**External rate limits are your real bottleneck, not Postgres.** The LLM provider, the search
API and YouTube will all throttle you long before the database does. You need a
provider-wide concurrency cap shared across all workers, not a per-process one. Postgres
advisory locks or a small token bucket table both work. Set a cap per provider, tune it from
observed 429s.

**Per user quotas.** Cap episodes per day and sources per episode from day one. Cheap now,
painful to retrofit once someone runs up a bill.

**Connection pooling.** Bounded SQLAlchemy pools, and PgBouncer in front once you have more
than a handful of worker replicas.

**What not to do.** No Kubernetes, no microservices, no Kafka, no separate vector database.
One Postgres plus N worker containers carries this product to a few thousand users
comfortably. Every one of those additions costs a two person team more than it returns.


## 5. Pipeline

### 5.1 Identify

Input: name plus disambiguator.
Search API call, then an LLM ranking pass over the results.
Output: candidates with name, headline, employer, photo URL, evidence URLs.
User selects. Selection creates the entity row and freezes it.

### 5.2 Discover

Given the confirmed entity, generate a source list. Query patterns:

- `"{name}" {employer}` news
- `"{name}" interview` and `"{name}" podcast`
- `"{name}" site:youtube.com`
- their own writing: blog, Substack, firm bio page
- if the guest is at a public company, filings and earnings call mentions

Cap at 25 candidate sources for v1. Dedupe by canonical URL.

### 5.3 Fetch and parse

One adapter per source type, all returning the same shape:

```python
class ParsedSource(TypedDict):
    title: str | None
    author: str | None
    published_at: datetime | None
    text: str
    segments: list[Segment] | None  # populated for AV sources, with ms offsets
```

Adapters for v1: `web_article`, `youtube`, `pdf`, `user_pasted`.

Rules:
- Save the raw payload to blob storage before parsing. You will reparse everything many
  times as extraction improves, and refetching is slow and sometimes impossible.
- Every job is idempotent and keyed on content checksum. Retries must not duplicate rows.
- A failed source does not fail the episode. Mark it failed, surface it in the UI, move on.

### 5.4 Chunk and embed

Target 600 to 900 tokens per chunk with overlap. Every chunk carries exact position:
character offsets for text, millisecond ranges for AV. A chunk that cannot point back to
a precise location in its source is useless and should not be stored.

### 5.5 Extract claims

Per chunk, one LLM call with structured JSON output:

```json
{
  "claims": [
    {
      "text": "atomic assertion, one idea",
      "kind": "biographical|opinion|fact|anecdote|prediction",
      "claim_date": "2025-03-11 or null",
      "verbatim_span": [start, end]
    }
  ]
}
```

Discard anything where `verbatim_span` does not resolve inside the chunk. This is the
guard against fabricated claims and it must be enforced in code, not in the prompt.

### 5.6 Cluster claims

Embed claim text, cluster by cosine similarity above threshold, confirm each cluster with
one LLM call. A cluster spanning 3 or more distinct sources goes into the
"already covered" section of the dossier. This is the single most valuable output in the
product and it is worth getting right before anything else.

### 5.7 Build dossier

Fixed sections, each one its own retrieval plus generation:

1. Career timeline, chronological, from biographical claims
2. Recent news, last 12 months
3. Public positions and how they have shifted over time
4. Already covered, from clusters with source_count >= 3
5. Unexplored angles, topics adjacent to their expertise with thin public coverage

Every generated sentence must return the claim ids it rests on. A verification pass drops
or flags any sentence with no claim id attached. No exceptions.

### 5.7b Coverage check and thin footprint mode

Most guests at small companies will have a thin public footprint. This is the common case,
not the edge case, and if the dossier just comes back empty the product looks broken.

After parsing and claim extraction, compute a coverage score for the guest entity:

- distinct sources that actually parsed
- distinct claim clusters
- date span of the claims
- whether any long form appearance exists (podcast, talk, webinar, long interview)

Map to a mode, thresholds in config:

| Mode | Behaviour |
|---|---|
| `rich` | Full guest dossier as specced. Script leans on guest specifics |
| `thin` | Show what was found, clearly labelled as limited. Prompt the user to paste a bio, past talks, internal notes or a CV. Weight the script toward topic and industry material |
| `sparse` | Guest section is a short factual summary only. The dossier becomes primarily a topic and industry brief. Tell the user plainly why |

Key point for the build: the topic and industry brief is **not a second system**. Create a
`topic` entity, run the exact same discover, fetch, chunk, extract, cluster pipeline against
it, and write a different composer. Everything upstream of composition is reused.

The UI must never present a thin result as though it were a full one. Say what was found,
say what was missing, and ask for the specific thing that would help.

### 5.8 Generate script

Inputs: topics, dossier, style preset, optional voice sample.

Output per segment:

```json
{
  "topic_id": "...",
  "question": "...",
  "rationale": "why this question, given what we know",
  "expected_direction": "where they will likely take it",
  "followups": ["if they say X, ask Y"],
  "risk_flags": ["already answered in 4 prior interviews", "regulatory sensitivity"],
  "claim_ids": ["..."]
}
```

Style presets: `formal`, `conversational`, `contrarian`, `educational`.
Voice sample path: extract style descriptors from the uploaded transcript in one call,
then pass those descriptors into generation. Do not paste the whole transcript into the
script prompt.

---

## 6. API

```
POST   /episodes
POST   /episodes/{id}/identify          -> candidates
POST   /episodes/{id}/confirm-guest     -> entity, kicks off discover
GET    /episodes/{id}                   -> episode, status, sources, job progress
POST   /episodes/{id}/sources           -> user pasted URL or text
DELETE /episodes/{id}/sources/{sid}
GET    /episodes/{id}/dossier
POST   /episodes/{id}/topics
POST   /episodes/{id}/script            -> style, optional voice sample
GET    /episodes/{id}/script
PATCH  /scripts/{id}/segments/{sid}     -> user edits
GET    /episodes/{id}/export?format=pdf
```

Long running work returns a job id. Client polls `GET /episodes/{id}`.

---

## 7. Repo layout

```
/api
  /adapters        source adapters, one file each
  /pipeline        identify, discover, chunk, extract, cluster, dossier, script
  /llm             provider interface, prompts, structured output helpers
  /models          sqlalchemy models
  /jobs            worker loop, job registry
  /routes
  /migrations
/web               next.js
/evals             fixtures and runner
```

---

## 8. Eval harness

Build this alongside 5.5, not after.

Ten real guests as fixtures. For each: a set of facts that must surface, and a set of
traps that must not (wrong person with the same name, outdated role, a claim from an
article that is actually about someone else).

Runner reports per fixture: recall on required facts, count of unsourced sentences,
count of trap hits. Run on every prompt or model change. Without this you cannot tell
whether a change helped, and with two people you will not notice silent regressions.

---

## 9. Acceptance criteria for v1

- Guest name plus LinkedIn URL produces a dossier in under 6 minutes.
- Every line in the dossier and the script links to a source the user can open.
- Zero unsourced sentences reach the UI.
- The "already covered" section correctly flags at least one repeated story for a guest
  with 5 or more prior public appearances.
- A failed source degrades the dossier, it never fails the run.
- A guest with fewer than 3 usable sources produces a labelled thin or sparse dossier plus
  a topic brief, never an empty page.
- Reparsing an existing episode from stored blobs requires no network fetches.

---

## 10. Environment

```
DATABASE_URL
ANTHROPIC_API_KEY
EMBEDDING_PROVIDER, EMBEDDING_API_KEY
SEARCH_API_KEY
BLOB_BACKEND=local|s3
S3_* (when s3)
```
