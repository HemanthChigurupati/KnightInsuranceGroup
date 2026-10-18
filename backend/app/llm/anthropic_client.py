"""Claude via the official Anthropic SDK."""

import anthropic

from app.llm.client import LLMError

MAX_TOKENS = 16000

# Models that accept server-side refusal fallbacks in the "default" form (Claude API).
# If a safety classifier declines, the API re-runs the request on a recommended model.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicClient:
    def __init__(self, model: str, api_key: str, timeout: float, effort: str = "medium"):
        self.model = model
        self.name = f"anthropic/{model}"
        self._effort = effort
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)

    def complete(self, system: str, user: str) -> str:
        kwargs: dict = {}
        if self.model in _FALLBACK_MODELS:
            kwargs = {"betas": [_FALLBACK_BETA], "fallbacks": "default"}
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self._effort},
                **kwargs,
            )
        except anthropic.AuthenticationError:
            raise LLMError("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY.") from None
        except anthropic.PermissionDeniedError:
            raise LLMError("The Anthropic API key lacks permission for this model.") from None
        except anthropic.NotFoundError:
            raise LLMError(f"Model not found: {self.model}. Check LLM_MODEL.") from None
        except anthropic.RateLimitError:
            raise LLMError("Rate limited by the Anthropic API. Try again shortly.") from None
        except anthropic.BadRequestError as exc:
            raise LLMError(f"The Anthropic API rejected the request: {exc.message}") from None
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error ({exc.status_code}). Try again later.") from None
        except anthropic.APITimeoutError:
            raise LLMError("The model call timed out.") from None
        except anthropic.APIConnectionError:
            raise LLMError("Could not reach the Anthropic API (network error).") from None

        if response.stop_reason == "refusal":
            raise LLMError("The model declined to process this document.")
        if response.stop_reason == "max_tokens":
            raise LLMError("The model response was cut off (max tokens reached).")
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise LLMError("The model returned an empty response.")
        return text
