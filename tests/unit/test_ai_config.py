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


def test_an_unimplemented_provider_name_is_rejected() -> None:
    """``openai`` used to be a Literal member that a validator then refused.

    It is simply not a member now, so the Literal itself refuses it and the
    message lists what *is* available — a better error, and one fewer validator.
    """
    with pytest.raises(ValidationError, match="Input should be"):
        _settings(ai_provider="openai")


def test_a_gateway_without_a_key_or_model_is_refused_at_boot() -> None:
    with pytest.raises(ValidationError, match="AVALAI_API_KEY"):
        _settings(ai_provider="avalai")
    with pytest.raises(ValidationError, match="AVALAI_MODEL"):
        _settings(ai_provider="avalai", avalai_api_key="k")


def test_a_fallback_without_a_key_is_refused_at_boot() -> None:
    """The failure this prevents: config that *looks* resilient until the day it matters."""
    with pytest.raises(ValidationError, match="GAPGPT_API_KEY"):
        _settings(
            ai_provider="avalai",
            avalai_api_key="k",
            avalai_model="m",
            ai_fallback_providers="gapgpt",
        )


def test_an_unknown_fallback_name_is_refused_and_lists_the_known_ones() -> None:
    with pytest.raises(ValidationError, match="Unknown AI provider 'nope'"):
        _settings(
            ai_provider="avalai",
            avalai_api_key="k",
            avalai_model="m",
            ai_fallback_providers="nope",
        )


def test_the_chain_is_ordered_primary_first_and_deduplicated() -> None:
    settings = _settings(
        ai_provider="avalai",
        avalai_api_key="k",
        avalai_model="m",
        gapgpt_api_key="k",
        gapgpt_model="m",
        tabitoken_api_key="k",
        tabitoken_model="m",
        ai_fallback_providers=" gapgpt , avalai ,TABITOKEN",
    )
    assert settings.provider_chain == ["avalai", "gapgpt", "tabitoken"]


@pytest.mark.parametrize("provider", ["stub", "anthropic"])
def test_non_openai_protocol_providers_have_no_chain(provider: str) -> None:
    """Neither can appear in a failover fleet: one is a different wire format,
    and the other would mask a real outage behind canned data."""
    settings = _settings(ai_provider=provider, anthropic_api_key="k")
    assert settings.provider_chain == []


def test_the_provider_registry_matches_the_settings_list() -> None:
    """The one thing the core/infrastructure split cannot enforce by import.

    ``config`` names the gateways as strings because it must not import
    ``infrastructure``; ``providers`` maps those names to classes. Adding one
    without the other fails here rather than at a learner's lookup.
    """
    from app.core.config import OPENAI_PROTOCOL_PROVIDERS
    from app.infrastructure.ai.providers import PROVIDERS

    assert sorted(PROVIDERS) == sorted(OPENAI_PROTOCOL_PROVIDERS)


def test_extra_headers_default_to_empty() -> None:
    assert _settings().anthropic_header_map == {}


def test_extra_headers_parse_a_json_object() -> None:
    settings = _settings(anthropic_extra_headers='{"user-agent": "claude-cli/2.1.0"}')
    assert settings.anthropic_header_map == {"user-agent": "claude-cli/2.1.0"}


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '{"a": 1}'])
def test_malformed_extra_headers_fail_at_startup(raw: str) -> None:
    with pytest.raises(ValidationError, match="ANTHROPIC_EXTRA_HEADERS"):
        _settings(anthropic_extra_headers=raw)


# ── The deck-build chain ──────────────────────────────────────


def test_no_build_provider_means_builds_use_the_request_chain() -> None:
    """A laptop and the test suite must build a deck with no extra config."""
    settings = _settings(ai_provider="avalai", avalai_api_key="k", avalai_model="m")
    assert settings.build_provider_chain == []


def test_the_build_chain_is_independent_of_the_request_chain() -> None:
    """The whole point: a published deck is written once and read by everyone,
    so it is worth a frontier model the interactive path cannot afford."""
    settings = _settings(
        ai_provider="avalai",
        avalai_api_key="k",
        avalai_model="gemini-3.5-flash-lite",
        tabitoken_api_key="k",
        tabitoken_model="claude-opus-5",
        agentrouter_api_key="k",
        agentrouter_model="claude-opus-5",
        ai_build_provider="tabitoken",
        ai_build_fallback_providers="agentrouter",
    )
    assert settings.provider_chain == ["avalai"]
    assert settings.build_provider_chain == ["tabitoken", "agentrouter"]


def test_a_build_gateway_without_a_key_is_refused_at_boot() -> None:
    """Same rule as the request path: an unusable gateway fails the deploy, not
    the first deck build that reaches for it an hour in."""
    with pytest.raises(ValidationError, match="TABITOKEN_API_KEY"):
        _settings(
            ai_provider="avalai",
            avalai_api_key="k",
            avalai_model="m",
            ai_build_provider="tabitoken",
        )


def test_a_build_chain_may_name_a_gateway_the_request_chain_also_uses() -> None:
    """They are separate lists, not a partition — sharing one gateway is legal."""
    settings = _settings(
        ai_provider="avalai",
        avalai_api_key="k",
        avalai_model="m",
        ai_build_provider="avalai",
    )
    assert settings.provider_chain == ["avalai"]
    assert settings.build_provider_chain == ["avalai"]
