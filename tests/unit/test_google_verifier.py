"""Google id_token verifier, exercised with locally-signed RS256 tokens."""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.exceptions import AuthenticationError
from app.infrastructure.auth.google_id_token_verifier import GoogleIdTokenVerifier

CLIENT_ID = "vocably-client-id.apps.googleusercontent.com"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _SigningKey:
    key = _PRIVATE_KEY.public_key()


def make_token(**overrides: Any) -> str:
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
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, _PRIVATE_KEY, algorithm="RS256")


@pytest.fixture
def verifier(monkeypatch: pytest.MonkeyPatch) -> GoogleIdTokenVerifier:
    instance = GoogleIdTokenVerifier(client_id=CLIENT_ID)
    monkeypatch.setattr(
        instance._jwk_client, "get_signing_key_from_jwt", lambda token: _SigningKey()
    )
    return instance


async def test_valid_token_yields_identity(verifier: GoogleIdTokenVerifier) -> None:
    identity = await verifier.verify(make_token())
    assert identity.sub == "1234567890"
    assert identity.email == "ali@example.com"
    assert identity.name == "Ali"


async def test_token_without_optional_claims_still_verifies(
    verifier: GoogleIdTokenVerifier,
) -> None:
    identity = await verifier.verify(make_token(email=None, name=None))
    assert identity.sub == "1234567890"
    assert identity.email is None
    assert identity.name is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "some-other-client"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 10},
        {"sub": None},
    ],
)
async def test_bad_tokens_are_rejected(
    verifier: GoogleIdTokenVerifier, overrides: dict[str, Any]
) -> None:
    with pytest.raises(AuthenticationError):
        await verifier.verify(make_token(**overrides))


async def test_tampered_token_is_rejected(verifier: GoogleIdTokenVerifier) -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    forged = jwt.encode(
        {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "x", "iat": now,
         "exp": now + 300},
        other_key,
        algorithm="RS256",
    )
    with pytest.raises(AuthenticationError):
        await verifier.verify(forged)
