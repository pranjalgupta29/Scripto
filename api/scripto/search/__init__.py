"""Web discovery. A search API for finding sources about the guest."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import lru_cache

import httpx

from scripto.config import settings


@dataclass
class SearchResult:
    url: str
    title: str | None = None
    snippet: str | None = None
    published_at: str | None = None
    author: str | None = None
    extra: dict = field(default_factory=dict)


class SearchProvider(ABC):
    @abstractmethod
    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        ...


class ExaProvider(SearchProvider):
    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        response = httpx.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": settings.search_api_key or ""},
            json={"query": query, "numResults": limit, "contents": {"text": False}},
            timeout=settings.http_timeout_seconds,
        )
        response.raise_for_status()
        return [
            SearchResult(
                url=r["url"],
                title=r.get("title"),
                snippet=r.get("text"),
                published_at=r.get("publishedDate"),
                author=r.get("author"),
            )
            for r in response.json().get("results", [])
        ]


class TavilyProvider(SearchProvider):
    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.search_api_key,
                "query": query,
                "max_results": limit,
            },
            timeout=settings.http_timeout_seconds,
        )
        response.raise_for_status()
        return [
            SearchResult(url=r["url"], title=r.get("title"), snippet=r.get("content"))
            for r in response.json().get("results", [])
        ]


class DuckDuckGoProvider(SearchProvider):
    """No key, no card, no signup.

    Unofficial: it reads DuckDuckGo's public endpoints rather than a supported
    API, so it is rate limited and can break when they change their markup.
    Good enough to develop against and to run low volume; move to Tavily or Exa
    before this matters to anyone but you.
    """

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException

        try:
            rows = list(DDGS().text(query, max_results=limit))
        except DDGSException as exc:
            # Ratelimits are common here. One failed query must never fail
            # discovery, which already tolerates per-query errors.
            raise RuntimeError(f"duckduckgo search failed: {exc}") from exc

        results: list[SearchResult] = []
        for row in rows:
            url = row.get("href") or row.get("url")
            if not url:
                continue
            results.append(
                SearchResult(
                    url=url,
                    title=row.get("title"),
                    snippet=row.get("body"),
                )
            )
        return results


class FakeSearchProvider(SearchProvider):
    """Deterministic results derived from the query.

    Returns a mix of article and youtube URLs so the adapter dispatch and the
    dedupe path are both exercised offline.
    """

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        slug = "".join(c if c.isalnum() else "-" for c in query.lower()).strip("-")[:60]
        results = [
            SearchResult(
                url=f"https://example.com/{slug}/article-{i}",
                title=f"Result {i} for {query}",
                snippet=f"A discussion of {query}.",
            )
            for i in range(1, min(limit, 4) + 1)
        ]
        results.append(
            SearchResult(
                url=f"https://www.youtube.com/watch?v=fake{abs(hash(slug)) % 10**8:08d}",
                title=f"Interview about {query}",
            )
        )
        return results[:limit]


@lru_cache
def get_search() -> SearchProvider:
    provider = settings.search_provider.lower()
    if provider == "exa":
        return ExaProvider()
    if provider == "tavily":
        return TavilyProvider()
    if provider in ("duckduckgo", "ddg"):
        return DuckDuckGoProvider()
    if provider == "fake":
        return FakeSearchProvider()
    raise ValueError(f"unknown SEARCH_PROVIDER: {settings.search_provider}")
