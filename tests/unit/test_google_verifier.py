"""Google id_token verifier, exercised against a mocked tokeninfo endpoint."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from app.core.exceptions import AuthenticationError
from app.infrastructure.auth.google_id_token_verifier import GoogleIdTokenVerifier

CLIENT_ID = "vocably-client-id.apps.googleusercontent.com"


def make_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1234567890",
        "email": "ali@example.com",
        "name": "Ali",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def make_verifier(handler: Any) -> GoogleIdTokenVerifier:
    transport = httpx.MockTransport(handler)
    return GoogleIdTokenVerifier(client_id=CLIENT_ID, transport=transport)


def tokeninfo_handler(claims: dict[str, Any] | None) -> Any:
    """A tokeninfo stand-in: 200+claims for a "valid" token, 400 otherwise."""

    def handler(request: httpx.Request) -> httpx.Response:
        if claims is None:
            return httpx.Response(400, json={"error_description": "Invalid Value"})
        return httpx.Response(200, json=claims)

    return handler


async def test_valid_token_yields_identity() -> None:
    verifier = make_verifier(tokeninfo_handler(make_claims()))
    identity = await verifier.verify("token")
    assert identity.sub == "1234567890"
    assert identity.email == "ali@example.com"
    assert identity.name == "Ali"


async def test_token_without_optional_claims_still_verifies() -> None:
    verifier = make_verifier(tokeninfo_handler(make_claims(email=None, name=None)))
    identity = await verifier.verify("token")
    assert identity.sub == "1234567890"
    assert identity.email is None
    assert identity.name is None


async def test_google_rejected_token_is_rejected() -> None:
    verifier = make_verifier(tokeninfo_handler(None))
    with pytest.raises(AuthenticationError):
        await verifier.verify("token")


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "some-other-client"},
        {"iss": "https://evil.example.com"},
        {"sub": None},
    ],
)
async def test_bad_claims_are_rejected(overrides: dict[str, Any]) -> None:
    verifier = make_verifier(tokeninfo_handler(make_claims(**overrides)))
    with pytest.raises(AuthenticationError):
        await verifier.verify("token")


async def test_network_failure_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    verifier = make_verifier(handler)
    with pytest.raises(AuthenticationError):
        await verifier.verify("token")


async def test_malformed_response_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    verifier = make_verifier(handler)
    with pytest.raises(AuthenticationError):
        await verifier.verify("token")
