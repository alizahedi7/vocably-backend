"""Application configuration, loaded from the environment via pydantic-settings."""

from __future__ import annotations

import json
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
    ai_provider: Literal["stub", "anthropic", "openai", "avalai"] = "stub"
    #: Required when AI_PROVIDER=anthropic. Never commit a value — set it in the
    #: environment. Rotate immediately if it leaks.
    anthropic_api_key: str = ""
    #: Override to route through a gateway/proxy that speaks the Anthropic API
    #: (e.g. https://agentrouter.org for dev). Empty = api.anthropic.com.
    anthropic_base_url: str = ""
    #: Extra headers sent on every provider request, as a JSON object. Some
    #: gateways gate on headers the SDK does not set by default; keeping this in
    #: config means swapping gateways never means editing the adapter.
    anthropic_extra_headers: str = ""
    #: Kept configurable because the right model changes faster than this code does.
    anthropic_model: str = "claude-opus-4-8"
    #: Lookup is interactive — the client shows a spinner, so fail fast rather than
    #: leaving the "Writing the back…" state up indefinitely.
    anthropic_timeout_seconds: float = 30.0
    #: Ceiling per lookup/story response. Generous enough for 4 full card backs.
    anthropic_max_tokens: int = 4096

    #: Required when AI_PROVIDER=avalai. Never commit a value — set it in the
    #: environment. Rotate immediately if it leaks.
    avalai_api_key: str = ""
    #: AvalAI (https://avalai.ir) speaks the OpenAI protocol, not Anthropic's.
    avalai_base_url: str = "https://api.avalai.ir/v1"
    #: No sensible default across an arbitrary gateway's model catalogue —
    #: required explicitly when AI_PROVIDER=avalai (validated below).
    avalai_model: str = ""
    avalai_timeout_seconds: float = 30.0
    #: Ceiling per lookup/story response. Generous enough for 4 full card backs.
    avalai_max_tokens: int = 4096

    # ── AI lookup cache ───────────────────────────────────────
    #: Vocabulary lookups repeat heavily across the user base, so the same
    #: provider response is otherwise bought over and over. Off only for
    #: debugging a provider in isolation — leaving it off in production means
    #: paying full price for every repeat of every word.
    ai_cache_enabled: bool = True
    #: How long an `unsupported` verdict is remembered. Real words never expire;
    #: unintelligible input does, so retry-spam is absorbed without letting the
    #: table fill with junk that will never be asked for again. 7 days.
    ai_cache_unsupported_ttl_seconds: int = 7 * 24 * 3600

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

    @model_validator(mode="after")
    def _validate_google_verifier(self) -> Settings:
        if self.is_production and self.google_verifier != "google":
            raise ValueError(
                "GOOGLE_VERIFIER must be 'google' when ENVIRONMENT=production — the "
                "'stub' verifier trusts any caller-supplied string as a Google identity."
            )
        return self

    @model_validator(mode="after")
    def _validate_ai_provider(self) -> Settings:
        if self.ai_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("AI_PROVIDER=anthropic requires ANTHROPIC_API_KEY.")
        if self.ai_provider == "openai":
            raise ValueError(
                "AI_PROVIDER=openai is not implemented — use 'stub', 'anthropic', or 'avalai'."
            )
        if self.ai_provider == "avalai" and not self.avalai_api_key:
            raise ValueError("AI_PROVIDER=avalai requires AVALAI_API_KEY.")
        if self.ai_provider == "avalai" and not self.avalai_model:
            raise ValueError("AI_PROVIDER=avalai requires AVALAI_MODEL.")
        self.anthropic_header_map  # noqa: B018 — fail fast on malformed JSON at startup
        return self

    @property
    def anthropic_header_map(self) -> dict[str, str]:
        """``anthropic_extra_headers`` parsed, or ``{}`` when unset."""
        if not self.anthropic_extra_headers.strip():
            return {}
        try:
            parsed = json.loads(self.anthropic_extra_headers)
        except json.JSONDecodeError as exc:
            raise ValueError(f"ANTHROPIC_EXTRA_HEADERS must be a JSON object: {exc}") from None
        if not isinstance(parsed, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
        ):
            raise ValueError("ANTHROPIC_EXTRA_HEADERS must be a JSON object of string values.")
        return parsed


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so settings are parsed once per process."""
    return Settings()


settings = get_settings()
