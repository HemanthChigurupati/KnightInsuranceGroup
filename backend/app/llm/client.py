"""The one interface the rest of the app uses to talk to a language model.

Any provider works if it can turn (system prompt, user prompt) into text.
Our own code validates that text, so no provider-specific features are needed.
"""

from typing import Protocol

from app.config import Settings


class LLMError(Exception):
    """A model call failed. The message is safe to show to the user (never contains a key)."""


class LLMClient(Protocol):
    name: str  # shown in the UI as "<provider>/<model>"

    def complete(self, system: str, user: str) -> str: ...


def get_llm_client(settings: Settings) -> LLMClient:
    if not settings.llm_configured:
        raise LLMError(
            f"No model configured for provider '{settings.llm_provider}'. "
            "Set LLM_MODEL and its key/base URL in .env (see .env.example)."
        )
    if settings.llm_provider == "anthropic":
        from app.llm.anthropic_client import AnthropicClient

        return AnthropicClient(
            model=settings.llm_model,
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
            effort=settings.llm_effort,
        )
    from app.llm.openai_compatible import OpenAICompatibleClient

    return OpenAICompatibleClient(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=(settings.llm_api_key.get_secret_value().strip() or None) if settings.llm_api_key else None,
        timeout=settings.llm_timeout_seconds,
    )
