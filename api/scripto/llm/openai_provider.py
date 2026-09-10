"""OpenAI-compatible provider.

The spec asks for an OpenAI-compatible interface so DeepSeek and others are drop
in. Point `OPENAI_BASE_URL` at any compatible endpoint. Claude itself goes
through `AnthropicProvider` and the official SDK, never through this shim.
"""

from __future__ import annotations

from typing import Any

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from scripto.config import settings
from scripto.llm.base import (
    LLMError,
    LLMProvider,
    Role,
    coerce_to_schema,
    parse_json_value,
)


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self) -> None:
        self._client = OpenAI(
            api_key=settings.openai_api_key or "unset",
            base_url=settings.openai_base_url,
            max_retries=5,  # free tiers return transient 503/429 often
        )

    def _model(self, role: Role) -> str:
        return {
            "extract": settings.llm_extract_model,
            "compose": settings.llm_compose_model,
            "rank": settings.llm_rank_model,
        }.get(role, settings.llm_compose_model)

    @property
    def version(self) -> str:
        return f"openai_compatible:{settings.llm_extract_model}/{settings.llm_compose_model}"

    def _effort(self, role: Role) -> str:
        """Reasoning budget for this role, empty when the parameter is unused."""
        if role == "compose":
            return settings.llm_compose_reasoning_effort.strip()
        return settings.llm_extract_reasoning_effort.strip()

    def _create(self, *, role: Role, system: str, prompt: str, max_tokens: int, **extra: Any) -> str:
        effort = self._effort(role)
        if effort:
            extra.setdefault("reasoning_effort", effort)
        def call(**kwargs: Any):
            return self._client.chat.completions.create(
                model=self._model(role),
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                **kwargs,
            )

        try:
            try:
                response = call(**extra)
            except APIStatusError as exc:
                # Not every OpenAI-compatible endpoint knows reasoning_effort.
                # Drop it and retry rather than failing over an optional knob.
                if exc.status_code == 400 and "reasoning_effort" in extra:
                    extra.pop("reasoning_effort")
                    response = call(**extra)
                else:
                    raise
        except RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}") from exc
        except APIStatusError as exc:
            raise LLMError(f"api error {exc.status_code}") from exc
        except APIConnectionError as exc:
            raise LLMError(f"connection error: {exc}") from exc
        return response.choices[0].message.content or ""

    def complete_json(
        self,
        *,
        role: Role,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Prefer schema-enforced output; fall back to plain JSON mode.

        `json_object` only guarantees *valid* JSON, not JSON matching our
        schema -- Gemini happily returns a bare top-level array for a schema
        whose root is an object. `json_schema` constrains the shape properly,
        but not every OpenAI-compatible endpoint implements it, so we degrade
        rather than hard-fail.
        """
        max_tokens = max_tokens or settings.llm_max_tokens
        name = schema.get("title", "response")

        try:
            raw = self._create(
                role=role,
                system=system,
                prompt=prompt,
                max_tokens=max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": name, "schema": schema, "strict": True},
                },
            )
        except LLMError:
            raw = self._create(
                role=role,
                system=system,
                prompt=prompt,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )

        return coerce_to_schema(parse_json_value(raw), schema)

    def complete_text(
        self, *, role: Role, system: str, prompt: str, max_tokens: int | None = None
    ) -> str:
        return self._create(
            role=role,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens or settings.llm_max_tokens,
        )
