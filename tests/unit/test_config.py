"""Validation guards on ``Settings`` that only fire under specific configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_fixed_otp_code_is_accepted_outside_production() -> None:
    settings = Settings(environment="development", otp_length=6, otp_fixed_code="123456")
    assert settings.otp_fixed_code == "123456"


def test_fixed_otp_code_forbidden_in_production() -> None:
    with pytest.raises(ValidationError, match="production"):
        Settings(environment="production", otp_fixed_code="123456")


def test_fixed_otp_code_must_match_otp_length() -> None:
    with pytest.raises(ValidationError, match="OTP_LENGTH"):
        Settings(environment="development", otp_length=6, otp_fixed_code="123")


def test_fixed_otp_code_must_be_digits() -> None:
    with pytest.raises(ValidationError, match="OTP_LENGTH"):
        Settings(environment="development", otp_length=6, otp_fixed_code="abcdef")
