"""Application settings, read from environment variables or the project-root .env file.

Secrets are never given defaults and are never returned by the API.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Which adapter to use: Claude via the Anthropic API, or any server that
    # speaks the OpenAI /v1/chat/completions format (OpenAI, Ollama, LM Studio, vLLM, ...).
    llm_provider: Literal["anthropic", "openai_compatible"] = "anthropic"
    llm_model: str = ""
    llm_timeout_seconds: float = 60.0
    # Claude only: reasoning effort. Extraction from short documents does not need more.
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"

    # anthropic
    anthropic_api_key: SecretStr | None = None

    # openai_compatible
    llm_base_url: str = ""
    llm_api_key: SecretStr | None = None  # optional: local servers such as Ollama need none

    @property
    def llm_configured(self) -> bool:
        if not self.llm_model:
            return False
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value().strip())
        return bool(self.llm_base_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
