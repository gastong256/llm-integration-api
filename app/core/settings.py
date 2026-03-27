from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    redis_url: str = "redis://localhost:6379"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str | None = None
    llm_timeout: int = 30
    cache_ttl: int = 300
    rate_limit_rpm: int = 60
    api_keys: str = "test-key-1,test-key-2"
    stub_failure_rate: float = 0.0
    llm_adapter: str = "stub"

    @property
    def api_keys_set(self) -> set[str]:
        return {key.strip() for key in self.api_keys.split(",") if key.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
