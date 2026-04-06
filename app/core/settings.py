from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    redis_url: str = "redis://localhost:6379"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str | None = None
    llm_timeout: int = Field(default=30, gt=0)
    cache_ttl: int = Field(default=300, gt=0)
    rate_limit_rpm: int = Field(default=60, gt=0)
    api_keys: str = "test-key-1,test-key-2"
    stub_failure_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    llm_adapter: Literal["stub", "http"] = "stub"
    llm_price_input_per_1k_tokens_usd: float = Field(default=0.00015, ge=0.0)
    llm_price_output_per_1k_tokens_usd: float = Field(default=0.0006, ge=0.0)
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "inference-api"

    @property
    def api_keys_set(self) -> set[str]:
        return {key.strip() for key in self.api_keys.split(",") if key.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
