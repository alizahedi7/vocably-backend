"""Adapter selection in the composition root (``app.api.deps``).

These branches only run under production-style configuration, so they are pinned
here: the wrong adapter (or a missing fail-fast guard) would otherwise surface
for the first time in production.
"""

from __future__ import annotations

import pytest

from app.api.deps import _google_id_token_verifier, get_google_verifier, get_otp_sender
from app.core.config import settings
from app.infrastructure.auth.console_otp_sender import ConsoleOTPSender
from app.infrastructure.auth.google_id_token_verifier import GoogleIdTokenVerifier
from app.infrastructure.auth.kavenegar_otp_sender import KavenegarOTPSender
from app.infrastructure.auth.stub_google_verifier import StubGoogleVerifier


def test_console_sender_is_the_default() -> None:
    assert isinstance(get_otp_sender(), ConsoleOTPSender)


def test_kavenegar_sender_is_selected_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "otp_sender", "kavenegar")
    monkeypatch.setattr(settings, "kavenegar_api_key", "key")
    monkeypatch.setattr(settings, "kavenegar_otp_template", "verify")

    assert isinstance(get_otp_sender(), KavenegarOTPSender)


def test_kavenegar_without_credentials_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "otp_sender", "kavenegar")
    monkeypatch.setattr(settings, "kavenegar_api_key", "")
    monkeypatch.setattr(settings, "kavenegar_otp_template", "")

    with pytest.raises(RuntimeError, match="KAVENEGAR_API_KEY"):
        get_otp_sender()


def test_stub_verifier_is_the_default() -> None:
    assert isinstance(get_google_verifier(), StubGoogleVerifier)


def test_google_verifier_is_selected_and_memoized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "google_verifier", "google")
    monkeypatch.setattr(settings, "google_client_id", "client.apps.googleusercontent.com")
    _google_id_token_verifier.cache_clear()
    try:
        verifier = get_google_verifier()
        assert isinstance(verifier, GoogleIdTokenVerifier)
        # Memoized so the JWKS key cache survives across requests.
        assert get_google_verifier() is verifier
    finally:
        _google_id_token_verifier.cache_clear()


def test_google_verifier_without_client_id_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "google_verifier", "google")
    monkeypatch.setattr(settings, "google_client_id", "")

    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID"):
        get_google_verifier()
