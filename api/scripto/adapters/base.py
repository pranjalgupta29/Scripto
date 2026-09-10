"""Adapter interface. One adapter per source type, all returning the same shape."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TypedDict
from urllib.parse import urlsplit, urlunsplit


class Segment(TypedDict):
    text: str
    start_ms: int
    end_ms: int
    speaker: str | None


class ParsedSource(TypedDict):
    title: str | None
    author: str | None
    published_at: datetime | None
    text: str
    segments: list[Segment] | None  # populated for AV sources, with ms offsets


class FetchError(RuntimeError):
    """A source failed to fetch or parse. Never fails the episode."""


class SourceAdapter(ABC):
    """Fetch stores the raw payload; parse works only from stored bytes.

    Keeping these separate is what makes `Reparsing an existing episode from
    stored blobs requires no network fetches` true.
    """

    type: str

    @abstractmethod
    def fetch(self, url: str) -> bytes:
        """Return the raw payload to be stored verbatim in blob storage."""

    @abstractmethod
    def parse(self, raw: bytes, *, url: str | None = None) -> ParsedSource:
        """Parse previously stored bytes. Must not touch the network."""


_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref_", "igshid")


def canonicalize_url(url: str) -> str:
    """Normalise a URL for dedupe.

    Sources are global and unique on canonical_url, so this function decides
    whether two users researching the same guest share a fetch.
    """
    url = url.strip()
    if not url:
        return url
    parts = urlsplit(url)
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    netloc = netloc.removesuffix(":443").removesuffix(":80")

    query = "&".join(
        q
        for q in parts.query.split("&")
        if q and not any(q.lower().startswith(p) for p in _TRACKING_PREFIXES)
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, query, ""))
