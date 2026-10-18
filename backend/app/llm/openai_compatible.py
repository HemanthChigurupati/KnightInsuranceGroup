"""Any server that speaks the OpenAI /v1/chat/completions format.

Covers OpenAI, Ollama (http://localhost:11434/v1), LM Studio, vLLM, Groq, OpenRouter, ...
"""

import httpx

from app.llm.client import LLMError


class OpenAICompatibleClient:
    def __init__(self, model: str, base_url: str, api_key: str | None, timeout: float):
        self.model = model
        self.name = f"openai_compatible/{model}"
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            resp = httpx.post(self._url, json=payload, headers=self._headers, timeout=self._timeout)
        except httpx.TimeoutException:
            raise LLMError("The model call timed out.") from None
        except httpx.HTTPError:
            raise LLMError(f"Could not reach the model server at {self._url}.") from None

        if resp.status_code in (401, 403):
            raise LLMError("The model server rejected the API key. Check LLM_API_KEY.")
        if resp.status_code == 404:
            raise LLMError(
                f"Model or endpoint not found ({self.model} at {self._url}).{_server_detail(resp)}"
            )
        if resp.status_code >= 400:
            raise LLMError(f"Model server error ({resp.status_code}).{_server_detail(resp)}")

        try:
            choice = resp.json()["choices"][0]
            text = choice["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("The model server returned an unexpected response format.") from None
        if choice.get("finish_reason") == "length":
            raise LLMError("The model response was cut off (max tokens reached).")
        if not text.strip():
            raise LLMError("The model returned an empty response.")
        return text


def _server_detail(resp: httpx.Response) -> str:
    """The server's own error message, if it sent one (e.g. "model retired, use X")."""
    try:
        body = resp.json()
        if isinstance(body, list):  # Gemini wraps the error in a list
            body = body[0]
        message = body["error"]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        return ""
    return f" Server says: {str(message)[:300]}"
