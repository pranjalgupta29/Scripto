"""LLM provider interface.

The spec wants the provider configurable per pipeline stage, with a cheap model
for extraction and a strong model for composition. Stages ask for a *role*
(`extract`, `compose`, `rank`) and the provider resolves it to a model id, so no
pipeline code ever hardcodes a model.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

Role = str  # extract | compose | rank


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    """Text in, structured JSON out. Every pipeline stage uses `complete_json`."""

    @abstractmethod
    def complete_json(
        self,
        *,
        role: Role,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Return a JSON object conforming to `schema`."""

    @abstractmethod
    def complete_text(
        self, *, role: Role, system: str, prompt: str, max_tokens: int | None = None
    ) -> str:
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Identifies provider+models. Stored on claims as extractor_version."""


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def coerce_to_schema(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    """Reshape a valid-but-wrong-shaped response into the schema's root object.

    Models under plain JSON mode often return the inner array directly -- e.g.
    `[{claim}, {claim}]` instead of `{"claims": [...]}`. When the schema has
    exactly one array-typed property the mapping is unambiguous, so we do it
    rather than throwing away a good response over packaging.
    """
    if isinstance(value, dict):
        return value

    if isinstance(value, list):
        array_props = [
            key
            for key, spec in (schema.get("properties") or {}).items()
            if isinstance(spec, dict) and spec.get("type") == "array"
        ]
        if len(array_props) == 1:
            return {array_props[0]: value}
        raise LLMError(
            f"expected a JSON object, got a list and schema "
            f"{schema.get('title')!r} has {len(array_props)} array properties"
        )

    raise LLMError(f"expected a JSON object, got {type(value).__name__}")


def parse_json_value(raw: str) -> Any:
    """Parse a response into whatever JSON value it holds (object or array)."""
    raw = raw.strip()
    if not raw:
        raise LLMError("empty response")

    for candidate in _json_candidates(raw):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise LLMError(f"could not parse JSON from response: {raw[:200]}")


def _json_candidates(raw: str):
    yield raw
    fenced = _FENCE.search(raw)
    if fenced:
        yield fenced.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = raw.find(opener), raw.rfind(closer)
        if start != -1 and end > start:
            yield raw[start : end + 1]


def parse_json_object(raw: str) -> dict[str, Any]:
    """Best-effort JSON extraction from a model response.

    Providers with native structured output return clean JSON; this is the
    fallback for those that wrap it in prose or a code fence.
    """
    raw = raw.strip()
    if not raw:
        raise LLMError("empty response")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    fenced = _FENCE.search(raw)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise LLMError(f"could not parse JSON from response: {raw[:200]}")
