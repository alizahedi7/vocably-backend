"""Adapter selection in the composition root (``app.api.deps``).

These branches only run under production-style configuration, so they are pinned
here: the wrong adapter (or a missing fail-fast guard) would otherwise surface
for the first time in production.
"""

from __future__ import annotations

import pytest

from app.api.deps import (
    _google_id_token_verifier,
    get_ai_provider,
    get_google_verifier,
    get_otp_sender,
)
from app.core.config import settings
from app.infrastructure.ai.avalai_ai_service import AvalAIService
from app.infrastructure.ai.factory import provider_for
from app.infrastructure.ai.failover_ai_service import FailoverAIService
from app.infrastructure.ai.stub_ai_service import StubAIService
from app.infrastructure.auth.console_otp_sender import ConsoleOTPSender
from app.infrastructure.auth.google_id_token_verifier import GoogleIdTokenVerifier
from app.infrastructure.auth.kavenegar_otp_sender import KavenegarOTPSender
from app.infrastructure.auth.sms_ir_otp_sender import SmsIrOTPSender
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


def test_sms_ir_sender_is_selected_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "otp_sender", "sms_ir")
    monkeypatch.setattr(settings, "sms_ir_api_key", "key")
    monkeypatch.setattr(settings, "sms_ir_template_id", 123456)

    assert isinstance(get_otp_sender(), SmsIrOTPSender)


def test_sms_ir_without_credentials_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "otp_sender", "sms_ir")
    monkeypatch.setattr(settings, "sms_ir_api_key", "")
    monkeypatch.setattr(settings, "sms_ir_template_id", 0)

    with pytest.raises(RuntimeError, match="SMS_IR_API_KEY"):
        get_otp_sender()


def test_stub_ai_service_is_the_default() -> None:
    assert isinstance(get_ai_provider(), StubAIService)


def test_avalai_service_is_selected_and_memoized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_provider", "avalai")
    monkeypatch.setattr(settings, "avalai_api_key", "key")
    monkeypatch.setattr(settings, "avalai_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "ai_fallback_providers", "")
    provider_for.cache_clear()
    try:
        service = get_ai_provider()
        assert isinstance(service, AvalAIService)
        # Cached so one HTTP client (and its connection pool) is shared.
        assert get_ai_provider() is service
    finally:
        provider_for.cache_clear()


def test_a_lone_gateway_is_not_wrapped_in_failover(monkeypatch: pytest.MonkeyPatch) -> None:
    """One gateway behind a failover loop is the same gateway plus a layer of logs."""
    monkeypatch.setattr(settings, "ai_provider", "avalai")
    monkeypatch.setattr(settings, "avalai_api_key", "key")
    monkeypatch.setattr(settings, "avalai_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "ai_fallback_providers", "")
    provider_for.cache_clear()
    try:
        assert not isinstance(get_ai_provider(), FailoverAIService)
    finally:
        provider_for.cache_clear()


def test_fallbacks_build_the_chain_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_provider", "avalai")
    monkeypatch.setattr(settings, "avalai_api_key", "key")
    monkeypatch.setattr(settings, "avalai_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "gapgpt_api_key", "key2")
    monkeypatch.setattr(settings, "gapgpt_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "ai_fallback_providers", "gapgpt")
    provider_for.cache_clear()
    try:
        service = get_ai_provider()
        assert isinstance(service, FailoverAIService)
        assert [p.name for p in service._providers] == ["avalai", "gapgpt"]
    finally:
        provider_for.cache_clear()


def test_naming_the_primary_again_as_a_fallback_does_not_duplicate_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config typo must not open two pools onto one gateway and call it twice."""
    monkeypatch.setattr(settings, "ai_provider", "avalai")
    monkeypatch.setattr(settings, "avalai_api_key", "key")
    monkeypatch.setattr(settings, "avalai_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "ai_fallback_providers", "avalai")

    assert settings.provider_chain == ["avalai"]


def test_avalai_without_credentials_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_provider", "avalai")
    monkeypatch.setattr(settings, "avalai_api_key", "")
    monkeypatch.setattr(settings, "avalai_model", "")
    monkeypatch.setattr(settings, "ai_fallback_providers", "")
    provider_for.cache_clear()

    with pytest.raises(ValueError, match="AVALAI_API_KEY"):
        get_ai_provider()


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
