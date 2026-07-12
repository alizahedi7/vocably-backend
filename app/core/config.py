"""Application configuration, loaded from the environment via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, computed_field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────
    project_name: str = "Vocably"
    environment: Environment = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    # NoDecode: skip pydantic-settings' JSON pre-parsing so the validator below can
    # accept a plain comma-separated string from the environment.
    backend_cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ── Security / JWT ────────────────────────────────────────
    secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # ── Database ──────────────────────────────────────────────
    database_url: str | None = None
    postgres_user: str = "vocably"
    postgres_password: str = "vocably"
    postgres_db: str = "vocably"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # ── Auth adapters ─────────────────────────────────────────
    otp_length: int = 6
    otp_ttl_seconds: int = 300
    otp_sender: Literal["console", "twilio"] = "console"
    google_verifier: Literal["stub", "google"] = "stub"
    google_client_id: str = ""

    # ── AI services ───────────────────────────────────────────
    ai_provider: Literal["stub", "anthropic", "openai"] = "stub"

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, value: object) -> object:
        """Accept a comma-separated string or a JSON/list for CORS origins."""
        if isinstance(value, str) and not value.startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_database_uri(self) -> str:
        """Async connection string; DATABASE_URL wins when explicitly provided."""
        if self.database_url:
            return self.database_url
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.postgres_user,
                password=self.postgres_password,
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so settings are parsed once per process."""
    return Settings()


settings = get_settings()
