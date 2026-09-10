"""YouTube adapter.

Transcript retrieval is a *swappable step*, not a hardcoded call. The adapter
walks an ordered list of `TranscriptStrategy` objects and takes the first that
returns segments. v1 ships captions only; Whisper slots in as one more strategy
in this list, and nothing in chunking, extraction or claims changes when it does.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from urllib.parse import parse_qs, urlsplit

from scripto.adapters.base import FetchError, ParsedSource, Segment, SourceAdapter


def video_id(url: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    if host == "youtu.be":
        return parts.path.lstrip("/")
    if "watch" in parts.path:
        vid = parse_qs(parts.query).get("v", [""])[0]
        if vid:
            return vid
    match = re.search(r"/(?:embed|shorts|v)/([A-Za-z0-9_-]{6,})", parts.path)
    if match:
        return match.group(1)
    raise FetchError(f"could not extract video id from {url!r}")


class TranscriptStrategy(ABC):
    """One way of getting a transcript. Ordered, first success wins."""

    name: str

    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def get(self, vid: str) -> list[Segment]:
        ...


class CaptionsStrategy(TranscriptStrategy):
    """YouTube's own captions. The only strategy enabled in v1."""

    name = "captions"

    def available(self) -> bool:
        return True

    def get(self, vid: str) -> list[Segment]:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import (
            NoTranscriptFound,
            TranscriptsDisabled,
            VideoUnavailable,
        )

        try:
            raw = YouTubeTranscriptApi().fetch(vid).to_raw_data()
        except (NoTranscriptFound, TranscriptsDisabled) as exc:
            raise FetchError(f"no captions available: {exc}") from exc
        except VideoUnavailable as exc:
            raise FetchError(f"video unavailable: {exc}") from exc

        segments: list[Segment] = []
        for entry in raw:
            start_ms = int(float(entry["start"]) * 1000)
            end_ms = start_ms + int(float(entry.get("duration", 0)) * 1000)
            text = (entry.get("text") or "").replace("\n", " ").strip()
            if text:
                segments.append(
                    Segment(text=text, start_ms=start_ms, end_ms=end_ms, speaker=None)
                )
        if not segments:
            raise FetchError("captions were empty")
        return segments


class WhisperStrategy(TranscriptStrategy):
    """Deferred to v1.5. This is the seam, deliberately left in place.

    Implementing this means downloading audio and calling Whisper here, and
    returning the same `Segment` list. No other module changes -- chunking reads
    ms offsets either way.
    """

    name = "whisper"

    def available(self) -> bool:
        return False  # flip on when the audio pipeline lands

    def get(self, vid: str) -> list[Segment]:
        raise FetchError("whisper transcription is not enabled in v1")


# Order matters: cheapest and most reliable first.
STRATEGIES: list[TranscriptStrategy] = [CaptionsStrategy(), WhisperStrategy()]


class YouTubeAdapter(SourceAdapter):
    type = "youtube"

    def __init__(self, strategies: list[TranscriptStrategy] | None = None) -> None:
        self._strategies = strategies if strategies is not None else STRATEGIES

    def fetch(self, url: str) -> bytes:
        """Store the transcript itself as the raw payload.

        Video bytes are not ours to keep, and the transcript is what every
        later stage reparses.
        """
        vid = video_id(url)
        errors: list[str] = []
        for strategy in self._strategies:
            if not strategy.available():
                continue
            try:
                segments = strategy.get(vid)
            except FetchError as exc:
                errors.append(f"{strategy.name}: {exc}")
                continue
            payload = {"video_id": vid, "strategy": strategy.name, "segments": segments}
            return json.dumps(payload).encode()

        raise FetchError(f"no transcript for {vid}: {'; '.join(errors) or 'no strategy available'}")

    def parse(self, raw: bytes, *, url: str | None = None) -> ParsedSource:
        payload = json.loads(raw.decode("utf-8"))
        segments: list[Segment] = payload["segments"]
        if not segments:
            raise FetchError("transcript had no segments")

        return ParsedSource(
            title=payload.get("title"),
            author=payload.get("channel"),
            published_at=None,
            text=" ".join(s["text"] for s in segments),
            segments=segments,
        )
