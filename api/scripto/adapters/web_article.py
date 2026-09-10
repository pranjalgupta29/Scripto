"""Web article adapter: fetch HTML verbatim, extract readable text on parse."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import trafilatura

from scripto.adapters.base import FetchError, ParsedSource, SourceAdapter
from scripto.config import settings


class WebArticleAdapter(SourceAdapter):
    type = "web_article"

    def fetch(self, url: str) -> bytes:
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": settings.http_user_agent},
                timeout=settings.http_timeout_seconds,
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise FetchError(f"fetch failed: {exc}") from exc
        return response.content

    def parse(self, raw: bytes, *, url: str | None = None) -> ParsedSource:
        html = raw.decode("utf-8", errors="replace")

        extracted = trafilatura.extract(
            html,
            url=url,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            favor_precision=True,
        )
        if not extracted:
            raise FetchError("no readable text extracted")

        data = json.loads(extracted)
        text = (data.get("text") or "").strip()
        if not text:
            raise FetchError("empty article text")

        return ParsedSource(
            title=data.get("title"),
            author=data.get("author"),
            published_at=_parse_date(data.get("date")),
            text=text,
            segments=None,
        )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(value[:25], fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
