"""Application settings.

Everything that differs between environments, and every credential, lives here
and nowhere else. Two settings are worth reading carefully.

``ingestion_enabled`` ships **false**. Crawling a third-party site is a legal
and relationship decision, not a deployment default (audit §4). Someone has to
read the source's terms and turn this on deliberately.

``reasoning_enabled`` can be turned off at any time and the product keeps
working — that is the invariant from audit §5, and having it as a plain boolean
makes it operationally real rather than theoretical.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application ---
    environment: Literal["local", "development", "staging", "production"] = "local"
    debug: bool = False
    app_name: str = "AutoIntel Azerbaijan"
    api_prefix: str = "/api/v1"
    default_language: Literal["az", "en", "ru"] = "az"

    # --- database ---
    database_url: str = "postgresql+psycopg://autointel:autointel@localhost:5432/autointel"
    database_echo: bool = False
    database_pool_size: int = 10

    # --- cache ---
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 900

    # --- reasoning layer ---
    reasoning_enabled: bool = True
    """When false the report is generated from the computed evidence alone.

    Every number and finding is unaffected; only the narrative prose changes.
    """

    grok_api_key: str = ""
    grok_model: str = "grok-4"
    """Configurable because model identifiers change. Set this to whichever
    model the account actually has access to."""

    grok_base_url: str = "https://api.x.ai/v1"
    grok_timeout_seconds: float = 60.0
    reasoning_max_attempts: int = 2

    # --- ingestion ---
    ingestion_enabled: bool = False
    """Ships disabled on purpose. Do not enable against a public source until
    that source's terms of service have been reviewed by a person and the
    decision recorded (audit §4)."""

    crawl_requests_per_second: float = 0.2
    """One request per five seconds, sustained. Conservative by design; the
    incremental strategy means we do not need volume."""

    crawl_burst: int = 3
    crawl_user_agent: str = "AutoIntelBot/0.1 (+https://autointel.az/bot)"
    """Identifiable, with a contact URL. We do not disguise the crawler."""

    crawl_timeout_seconds: float = 30.0
    crawl_max_pages_per_run: int = 500
    robots_cache_seconds: int = 3600

    # --- analysis policy ---
    comparable_target_sample: int = 25
    comparable_min_sample: int = 5
    observation_window_days: int = 180

    # --- security ---
    secret_key: str = Field(default="", repr=False)
    admin_api_key: str = Field(default="", repr=False)
    """Gates the operator endpoints. Unset means they are disabled entirely
    rather than open — an operator endpoint that ships unauthenticated because
    a variable was forgotten is the worse of the two failures."""

    access_token_ttl_minutes: int = 60
    refresh_token_ttl_days: int = 30
    cors_origins: list[str] = ["http://localhost:3000"]
    rate_limit_per_minute: int = 60
    anonymous_rate_limit_per_minute: int = 10
    max_upload_bytes: int = 10 * 1024 * 1024

    @field_validator("secret_key")
    @classmethod
    def _secret_required_outside_local(cls, value: str, info) -> str:  # type: ignore[no-untyped-def]
        """Refuse to start in a deployed environment without a real secret.

        A default signing key that reaches production is a silent, total
        authentication bypass. Failing at startup is the only safe behaviour.
        """
        environment = (info.data or {}).get("environment", "local")
        if environment in ("staging", "production") and len(value) < 32:
            raise ValueError(
                "SECRET_KEY must be set to at least 32 characters in staging and production"
            )
        return value

    @field_validator("crawl_requests_per_second")
    @classmethod
    def _cap_crawl_rate(cls, value: float) -> float:
        """Hard ceiling on crawl rate, regardless of configuration.

        Politeness must not be a knob somebody can turn up during an incident.
        """
        if value <= 0:
            raise ValueError("crawl_requests_per_second must be positive")
        return min(value, 2.0)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def reasoning_configured(self) -> bool:
        return self.reasoning_enabled and bool(self.grok_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
