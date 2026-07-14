"""OTP generation, including the dev/test fixed-code override."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.security import generate_otp


def test_generates_a_code_of_the_configured_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "otp_length", 6)
    monkeypatch.setattr(settings, "otp_fixed_code", "")

    code = generate_otp()
    assert len(code) == 6
    assert code.isdigit()


def test_fixed_code_overrides_random_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "otp_fixed_code", "123456")

    assert generate_otp() == "123456"
    assert generate_otp() == "123456"  # deterministic, not just first-call luck
