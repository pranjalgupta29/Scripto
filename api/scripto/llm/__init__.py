from functools import lru_cache

from scripto.config import settings
from scripto.llm.base import LLMError, LLMProvider, parse_json_object


@lru_cache
def get_llm() -> LLMProvider:
    provider = settings.llm_provider.lower()
    if provider == "anthropic":
        from scripto.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if provider in ("openai", "openai_compatible"):
        from scripto.llm.openai_provider import OpenAICompatibleProvider

        return OpenAICompatibleProvider()
    if provider == "fake":
        from scripto.llm.fake import FakeProvider

        return FakeProvider()
    raise ValueError(f"unknown LLM_PROVIDER: {settings.llm_provider}")


__all__ = ["get_llm", "LLMProvider", "LLMError", "parse_json_object"]
