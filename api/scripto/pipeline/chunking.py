"""5.4 Chunking.

Target 600-900 tokens with overlap. Every chunk carries an exact position:
character offsets for text, millisecond ranges for AV. A chunk that cannot point
back to a precise location in its source is useless and is never produced here.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, TypedDict

from scripto.adapters.base import ParsedSource
from scripto.config import settings


class ChunkDict(TypedDict, total=False):
    text: str
    start_offset: int
    end_offset: int
    start_ms: int
    end_ms: int
    speaker: str | None


@lru_cache
def _encoder():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoder().encode(text, disallowed_special=()))


# Sentence boundaries, keeping the delimiter with the sentence.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of sentences, covering the whole string with no gaps."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE_END.finditer(text):
        spans.append((cursor, match.start()))
        cursor = match.end()
    if cursor < len(text):
        spans.append((cursor, len(text)))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def chunk_text(text: str, *, target: int | None = None, overlap: int | None = None) -> list[ChunkDict]:
    """Chunk plain text on sentence boundaries, carrying character offsets."""
    target = target or settings.chunk_target_tokens
    overlap = overlap or settings.chunk_overlap_tokens

    spans = _sentence_spans(text)
    if not spans:
        return []

    chunks: list[ChunkDict] = []
    current: list[tuple[int, int]] = []
    current_tokens = 0

    for span in spans:
        span_tokens = count_tokens(text[span[0] : span[1]])

        # A single sentence longer than the target becomes its own chunk rather
        # than being split mid-sentence and losing its offsets.
        if span_tokens >= target and not current:
            chunks.append(_emit(text, [span]))
            continue

        if current and current_tokens + span_tokens > target:
            chunks.append(_emit(text, current))
            current, current_tokens = _carry_overlap(text, current, overlap)

        current.append(span)
        current_tokens += span_tokens

    if current:
        chunks.append(_emit(text, current))
    return chunks


def _emit(text: str, spans: list[tuple[int, int]]) -> ChunkDict:
    start, end = spans[0][0], spans[-1][1]
    return ChunkDict(text=text[start:end], start_offset=start, end_offset=end)


def _carry_overlap(
    text: str, spans: list[tuple[int, int]], overlap: int
) -> tuple[list[tuple[int, int]], int]:
    """Keep trailing sentences as the next chunk's lead-in."""
    kept: list[tuple[int, int]] = []
    total = 0
    for span in reversed(spans):
        tokens = count_tokens(text[span[0] : span[1]])
        if total + tokens > overlap:
            break
        kept.insert(0, span)
        total += tokens
    return kept, total


def chunk_segments(segments: list[dict[str, Any]], *, target: int | None = None) -> list[ChunkDict]:
    """Chunk AV segments, carrying millisecond ranges.

    Caption cues are far too small to be useful units, so they are packed up to
    the token target while the ms range spans the whole group.
    """
    target = target or settings.chunk_target_tokens
    chunks: list[ChunkDict] = []
    buffer: list[dict[str, Any]] = []
    tokens = 0

    def flush() -> None:
        nonlocal buffer, tokens
        if not buffer:
            return
        speakers = {s.get("speaker") for s in buffer if s.get("speaker")}
        chunks.append(
            ChunkDict(
                text=" ".join(s["text"] for s in buffer).strip(),
                start_ms=int(buffer[0]["start_ms"]),
                end_ms=int(buffer[-1]["end_ms"]),
                speaker=speakers.pop() if len(speakers) == 1 else None,
            )
        )
        buffer, tokens = [], 0

    for segment in segments:
        segment_tokens = count_tokens(segment["text"])
        if buffer and tokens + segment_tokens > target:
            flush()
        buffer.append(segment)
        tokens += segment_tokens

    flush()
    return [c for c in chunks if c["text"]]


def chunk_parsed_source(parsed: ParsedSource) -> list[ChunkDict]:
    """Dispatch on what the adapter produced. Exactly one position pair is set."""
    if parsed.get("segments"):
        return chunk_segments(parsed["segments"])
    return chunk_text(parsed["text"])
