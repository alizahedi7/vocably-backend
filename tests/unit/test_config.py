"""Validation guards on ``Settings`` that only fire under specific configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_fixed_otp_code_is_accepted_outside_production() -> None:
    settings = Settings(environment="development", otp_length=6, otp_fixed_code="123456")
    assert settings.otp_fixed_code == "123456"


def test_fixed_otp_code_forbidden_in_production() -> None:
    with pytest.raises(ValidationError, match="OTP_FIXED_CODE"):
        Settings(environment="production", debug=False, otp_fixed_code="123456")


def test_fixed_otp_code_must_match_otp_length() -> None:
    with pytest.raises(ValidationError, match="OTP_LENGTH"):
        Settings(environment="development", otp_length=6, otp_fixed_code="123")


def test_fixed_otp_code_must_be_digits() -> None:
    with pytest.raises(ValidationError, match="OTP_LENGTH"):
        Settings(environment="development", otp_length=6, otp_fixed_code="abcdef")


def test_stub_google_verifier_forbidden_in_production() -> None:
    with pytest.raises(ValidationError, match="GOOGLE_VERIFIER"):
        Settings(environment="production", debug=False, google_verifier="stub")


def test_google_verifier_accepted_in_production() -> None:
    settings = Settings(
        environment="production",
        debug=False,
        google_verifier="google",
        google_client_id="client-id",
    )
    assert settings.google_verifier == "google"


def test_stub_google_verifier_accepted_outside_production() -> None:
    settings = Settings(environment="development", google_verifier="stub")
    assert settings.google_verifier == "stub"


# ── Production hardening guards ───────────────────────────────
#
# Every one of these fires only when ENVIRONMENT=production, so a developer's
# laptop and the test suite are untouched. They exist because the failure mode
# of each is silent: the app boots, serves traffic, and looks entirely healthy
# while being wrong.


def production_settings(**overrides: object) -> Settings:
    """A production config that satisfies every guard, for the positive cases.

    The negative cases below construct ``Settings`` directly instead: each names
    the one field under test and relies on its validator firing first, so a test
    can never pass on some *other* guard's error message.
    """
    return Settings(  # type: ignore[arg-type]
        environment="production",
        debug=False,
        secret_key="b" * 64,
        google_verifier="google",
        google_client_id="client-id",
        **overrides,
    )


def test_placeholder_secret_key_forbidden_in_production() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(environment="production", debug=False, secret_key="change-me")


def test_env_example_secret_key_placeholder_is_also_rejected() -> None:
    """The .env.example value is long enough to pass a length check on its own.

    Copying that file to the VPS and forgetting this one line is the realistic
    path to a forgeable-token production, so the substring match matters as much
    as the length floor.
    """
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(
            environment="production",
            debug=False,
            secret_key="change-me-in-production-please-use-a-long-random-string",
        )


def test_short_secret_key_forbidden_in_production() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(environment="production", debug=False, secret_key="a" * 31)


def test_long_random_secret_key_accepted_in_production() -> None:
    assert production_settings().secret_key == "b" * 64


def test_weak_secret_key_accepted_outside_production() -> None:
    """The default has to keep working for `uv run uvicorn` with no .env at all."""
    settings = Settings(environment="development", secret_key="change-me")
    assert settings.secret_key == "change-me"


def test_debug_forbidden_in_production() -> None:
    with pytest.raises(ValidationError, match="DEBUG"):
        Settings(environment="production", debug=True, secret_key="b" * 64)


def test_wildcard_cors_origin_forbidden_in_production() -> None:
    with pytest.raises(ValidationError, match="BACKEND_CORS_ORIGINS"):
        Settings(
            environment="production",
            debug=False,
            secret_key="b" * 64,
            backend_cors_origins="https://app.vocably.ir,*",
        )


def test_explicit_cors_origins_accepted_in_production() -> None:
    settings = production_settings(backend_cors_origins="https://app.vocably.ir")
    assert settings.backend_cors_origins == ["https://app.vocably.ir"]


def test_wildcard_cors_origin_accepted_outside_production() -> None:
    settings = Settings(environment="development", backend_cors_origins="*")
    assert settings.backend_cors_origins == ["*"]


# ── API docs exposure ─────────────────────────────────────────


def test_docs_are_off_by_default_in_production() -> None:
    assert production_settings().docs_enabled is False


def test_docs_are_on_by_default_outside_production() -> None:
    assert Settings(environment="development").docs_enabled is True
    assert Settings(environment="staging").docs_enabled is True


def test_expose_docs_overrides_the_environment_in_both_directions() -> None:
    """A staging box turns them on; a locked-down dev box turns them off."""
    assert production_settings(expose_docs=True).docs_enabled is True
    assert Settings(environment="development", expose_docs=False).docs_enabled is False
