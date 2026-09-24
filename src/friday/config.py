"""Validated, environment-driven configuration; API keys never enter log output."""

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FRIDAY_", env_file=".env", extra="ignore")

    provider: Literal["fake", "gemini"] = "fake"
    model: str = "gemini-3.8-live"
    voice: str = "Kore"
    search_model: str = "gemini-3.8-flash"
    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    # BaseSettings reads environment values as strings; plain int enables parsing.
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    max_session_seconds: int = Field(default=300, ge=5, le=3600)
    event_queue_size: int = Field(default=128, ge=4, le=4096)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("model", "voice", "search_model")
    @classmethod
    def must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("cannot be empty")
        return value.strip()

    @field_validator("input_sample_rate")
    @classmethod
    def validate_input_sample_rate(cls, value: int) -> int:
        if value != 16000:
            raise ValueError("FRIDAY_INPUT_SAMPLE_RATE must be 16000")
        return value

    @field_validator("output_sample_rate")
    @classmethod
    def validate_output_sample_rate(cls, value: int) -> int:
        if value != 24000:
            raise ValueError("FRIDAY_OUTPUT_SAMPLE_RATE must be 24000")
        return value

    def require_gemini_key(self) -> str:
        if self.gemini_api_key is None or not self.gemini_api_key.get_secret_value().strip():
            raise ValueError("GEMINI_API_KEY is required for the Gemini provider")
        return self.gemini_api_key.get_secret_value()
