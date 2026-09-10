"""Anthropic provider, via the official SDK.

Uses structured outputs (`output_config.format`) so the model returns JSON
matching the caller's schema instead of prose we have to salvage.
"""

from __future__ import annotations

from typing import Any

import anthropic

from scripto.config import settings
from scripto.llm.base import LLMError, LLMProvider, Role, parse_json_object


class AnthropicProvider(LLMProvider):
    def __init__(self) -> None:
        # Zero-arg construction also picks up an `ant auth login` profile,
        # so an unset ANTHROPIC_API_KEY is not necessarily a missing credential.
        kwargs: dict[str, Any] = {"max_retries": 3}
        if settings.anthropic_api_key:
            kwargs["api_key"] = settings.anthropic_api_key
        self._client = anthropic.Anthropic(**kwargs)

    def _model(self, role: Role) -> str:
        return {
            "extract": settings.llm_extract_model,
            "compose": settings.llm_compose_model,
            "rank": settings.llm_rank_model,
        }.get(role, settings.llm_compose_model)

    @property
    def version(self) -> str:
        return f"anthropic:{settings.llm_extract_model}/{settings.llm_compose_model}"

    def _create(
        self, *, role: Role, system: str, prompt: str, max_tokens: int, **extra: Any
    ) -> anthropic.types.Message:
        try:
            return self._client.messages.create(
                model=self._model(role),
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                **extra,
            )
        except anthropic.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"api error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"connection error: {exc}") from exc

    def complete_json(
        self,
        *,
        role: Role,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        response = self._create(
            role=role,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens or settings.llm_max_tokens,
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": schema,
                }
            },
        )
        if response.stop_reason == "refusal":
            detail = getattr(response.stop_details, "category", None)
            raise LLMError(f"model refused the request ({detail})")

        text = "".join(b.text for b in response.content if b.type == "text")
        return parse_json_object(text)

    def complete_text(
        self, *, role: Role, system: str, prompt: str, max_tokens: int | None = None
    ) -> str:
        response = self._create(
            role=role,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens or settings.llm_max_tokens,
        )
        if response.stop_reason == "refusal":
            raise LLMError("model refused the request")
        return "".join(b.text for b in response.content if b.type == "text")
