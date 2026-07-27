"""Config validation for the AI provider selection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings(**overrides: object) -> Settings:
    return Settings(secret_key="x" * 32, **overrides)  # type: ignore[arg-type]


def test_anthropic_provider_requires_an_api_key() -> None:
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        _settings(ai_provider="anthropic")


def test_openai_provider_is_rejected_until_implemented() -> None:
    with pytest.raises(ValidationError, match="not implemented"):
        _settings(ai_provider="openai")


def test_extra_headers_default_to_empty() -> None:
    assert _settings().anthropic_header_map == {}


def test_extra_headers_parse_a_json_object() -> None:
    settings = _settings(anthropic_extra_headers='{"user-agent": "claude-cli/2.1.0"}')
    assert settings.anthropic_header_map == {"user-agent": "claude-cli/2.1.0"}


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '{"a": 1}'])
def test_malformed_extra_headers_fail_at_startup(raw: str) -> None:
    with pytest.raises(ValidationError, match="ANTHROPIC_EXTRA_HEADERS"):
        _settings(anthropic_extra_headers=raw)
