"""Application configuration, loaded from the environment via pydantic-settings."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "staging", "production", "test"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # ENV_FILE lets the test suite point this at os.devnull so a developer's
        # local .env (e.g. real Google/Kavenegar credentials for manual testing)
        # never leaks into test runs. See tests/conftest.py.
        env_file=os.environ.get("ENV_FILE", ".env"),
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
    otp_resend_cooldown_seconds: int = 30
    # Cost-abuse backstop: SMS requests allowed per client IP per hour (<= 0 disables).
    otp_requests_per_ip_per_hour: int = 20
    # DEV/TEST ONLY: issue this exact code instead of a random one, for every phone
    # number, so mobile/QA can sign in without reading server logs. Forbidden in
    # production (validated below) — it disables OTP secrecy entirely while set.
    otp_fixed_code: str = ""
    otp_sender: Literal["console", "kavenegar", "sms_ir"] = "console"
    kavenegar_api_key: str = ""
    kavenegar_otp_template: str = ""
    sms_ir_api_key: str = ""
    sms_ir_template_id: int = 0
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

    @model_validator(mode="after")
    def _validate_fixed_otp_code(self) -> Settings:
        if not self.otp_fixed_code:
            return self
        if self.is_production:
            raise ValueError(
                "OTP_FIXED_CODE must not be set when ENVIRONMENT=production — "
                "it disables OTP secrecy for every phone number."
            )
        if not self.otp_fixed_code.isdigit() or len(self.otp_fixed_code) != self.otp_length:
            raise ValueError(
                f"OTP_FIXED_CODE must be exactly {self.otp_length} digits (OTP_LENGTH)."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so settings are parsed once per process."""
    return Settings()


settings = get_settings()
