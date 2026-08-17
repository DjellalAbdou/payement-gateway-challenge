from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="GATEWAY_", extra="ignore"
    )

    # -- Acquiring bank settings ---------------
    acquiring_bank_url: str = "http://localhost:8080"
    bank_connect_timeout_seconds: float = 2.0
    bank_read_timeout_seconds: float = 10.0
    bank_max_attempts: int = Field(default=3, ge=1)
    bank_retry_backoff_seconds: float = 0.2

    # -- Payment rules -------------------------
    supported_currencies: frozenset[str] = frozenset({"USD", "EUR", "GBP"})
    max_amount_minor_units: int = 100_000_000_000
    min_amount_minor_units: int = 1

    # -- Merchant auths ---------------------
    api_keys: dict[str, str] = Field(
        default={
            "sk_test_alpha": "merchant_alpha",
            "sk_test_beta": "merchant_beta",
        }
    )

    # -- Idempotency key settings ----------------
    idempotency_key_ttl_seconds: int = 3600  # 1 hour
    idempotency_secret_key: str = (
        "super_secret_key"  # used for HMAC fingerprinting of idempotency keys
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
