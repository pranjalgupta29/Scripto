"""Adapter registry and source-type dispatch."""

from __future__ import annotations

from urllib.parse import urlsplit

from scripto.adapters.base import (
    FetchError,
    ParsedSource,
    Segment,
    SourceAdapter,
    canonicalize_url,
)
from scripto.adapters.simple import DocxAdapter, PdfAdapter, UserPastedAdapter
from scripto.adapters.web_article import WebArticleAdapter
from scripto.adapters.youtube import YouTubeAdapter

_ADAPTERS: dict[str, SourceAdapter] = {
    "web_article": WebArticleAdapter(),
    "youtube": YouTubeAdapter(),
    "pdf": PdfAdapter(),
    "docx": DocxAdapter(),
    "user_pasted": UserPastedAdapter(),
    # A profile page is just an article we treat as authoritative about the guest.
    "profile": WebArticleAdapter(),
}

_YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}


def get_adapter(source_type: str) -> SourceAdapter:
    if source_type not in _ADAPTERS:
        raise FetchError(f"no adapter for source type {source_type!r}")
    return _ADAPTERS[source_type]


def classify_url(url: str) -> str:
    """Pick a source type from a URL."""
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    if host in _YOUTUBE_HOSTS:
        return "youtube"
    if parts.path.lower().endswith(".pdf"):
        return "pdf"
    if host in ("linkedin.com", "x.com", "twitter.com") or "/in/" in parts.path:
        return "profile"
    return "web_article"


__all__ = [
    "FetchError",
    "ParsedSource",
    "Segment",
    "SourceAdapter",
    "canonicalize_url",
    "classify_url",
    "get_adapter",
]
