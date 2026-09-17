"""All configuration comes from the environment. No local-only shortcuts.

Every path, secret and service address is read here so the same image runs
unchanged in dev and in production.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# api/.env, located from this file rather than the process's working directory.
# A worker started from another directory once missed it and silently fell back
# to defaults that pointed at a different Postgres on port 5432.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # --- core ---
    # Required, with no default: a missing database setting should stop the
    # process at startup, not surface later as an auth error from the wrong server.
    database_url: str
    jwt_secret: str = "dev-only-change-me"
    jwt_ttl_hours: int = 24 * 14

    # --- blob storage ---
    blob_backend: str = "local"  # local | s3
    blob_local_root: str = "./var/blobs"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    # --- llm ---
    # Provider is configurable per pipeline stage. Cheap model for extraction,
    # strong model for composition.
    llm_provider: str = "fake"  # anthropic | openai_compatible | fake
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"

    # Spec: cheap model for extraction, strong model for composition.
    llm_extract_model: str = "claude-haiku-4-5"
    llm_compose_model: str = "claude-opus-5"
    llm_rank_model: str = "claude-haiku-4-5"
    # Generous on purpose: reasoning models spend tokens before emitting
    # output, and a truncated response is an unparseable one.
    llm_max_tokens: int = 16000

    # Reasoning budget for providers that expose one ("none" | "low" | "medium"
    # | "high"; empty string = do not send the parameter).
    #
    # Claim extraction is mechanical -- locate assertions, record their offsets
    # -- and deliberation buys nothing while costing a great deal of latency.
    # Composition is where judgement actually matters, so it gets a budget.
    llm_extract_reasoning_effort: str = ""
    llm_compose_reasoning_effort: str = ""

    # --- embeddings ---
    embedding_provider: str = "fake"  # voyage | openai | fake
    embedding_api_key: str | None = None
    embedding_model: str = "voyage-3"
    embedding_dim: int = 1024

    # --- web discovery ---
    search_provider: str = "fake"  # exa | tavily | fake
    search_api_key: str | None = None

    # --- pipeline tuning ---
    max_sources_per_episode: int = 25
    max_episodes_per_user_per_day: int = 10
    chunk_target_tokens: int = 750
    chunk_overlap_tokens: int = 100
    cluster_similarity_threshold: float = 0.86
    already_covered_min_sources: int = 3

    # coverage thresholds -> rich | thin | sparse
    # "rich" needs this share of the source limit readable (coverage.rich_min_sources)
    coverage_rich_share: float = 0.75
    coverage_rich_min_clusters: int = 15
    coverage_thin_min_sources: int = 3

    # --- worker / queue ---
    worker_poll_interval_seconds: float = 1.0
    job_lease_seconds: int = 600
    job_max_attempts: int = 4
    worker_concurrency: int = 4

    # provider-wide concurrency caps, shared across all workers via advisory locks
    provider_cap_llm: int = 8
    provider_cap_search: int = 2
    provider_cap_fetch: int = 6
    provider_cap_embedding: int = 4

    # Hard monthly ceilings on billable calls, enforced in code before the call
    # is made. 0 disables a provider entirely; -1 means unlimited.
    # Deliberately conservative: raise them once you trust your spend.
    budget_search_calls_per_month: int = 300
    budget_llm_calls_per_month: int = 2000
    budget_embedding_calls_per_month: int = 500
    budget_fetch_calls_per_month: int = -1  # fetching costs nothing

    # Requests-per-minute ceilings, paced across all workers. 0 = unpaced.
    # A concurrency cap does not bound request rate: two workers making 2s
    # calls sustain ~60 rpm, which most free tiers refuse.
    provider_rpm_llm: int = 0
    provider_rpm_search: int = 0
    provider_rpm_embedding: int = 0
    provider_rpm_fetch: int = 0

    # --- uploads ---
    # The host's own material: a resume, a bio, a LinkedIn "Save to PDF" export.
    max_upload_bytes: int = 10 * 1024 * 1024

    # --- misc ---
    http_user_agent: str = "ScriptoBot/0.1 (+research)"
    http_timeout_seconds: float = 30.0
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
