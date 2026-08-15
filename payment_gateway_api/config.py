from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="GATEWAY_", extra="ignore"
    )

    # -- Acquiring bank settings ---------------
    acquiring_bank_url: str = "http://localhost:8000"
    bank_connect_timeout_seconds: float = 2.0
    bank_read_timeout_seconds: float = 10.0
    bank_max_attempts: int = Field(default=3, ge=1)
    bank_retry_backoff_seconds: float = 0.2

    # -- Payment rules -------------------------
    supported_currencies: frozenset[str] = frozenset({"USD", "EUR", "GBP"})


@lru_cache
def get_settings() -> Settings:
    return Settings()
