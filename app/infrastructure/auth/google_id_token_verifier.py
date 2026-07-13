"""Google verifier that validates id_tokens against Google's published JWKS.

Selected via ``GOOGLE_VERIFIER=google``; requires ``GOOGLE_CLIENT_ID`` (the token
audience). The JWKS client caches Google's signing keys between calls.
"""

from __future__ import annotations

import asyncio
from typing import Any

import jwt

from app.application.ports.google_verifier import GoogleIdentity, GoogleVerifier
from app.core.exceptions import AuthenticationError

_GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleIdTokenVerifier(GoogleVerifier):
    def __init__(self, client_id: str, jwks_url: str = _GOOGLE_JWKS_URL) -> None:
        self._client_id = client_id
        self._jwk_client = jwt.PyJWKClient(jwks_url, cache_keys=True)

    async def verify(self, id_token: str) -> GoogleIdentity:
        # PyJWKClient fetches keys with blocking I/O; keep it off the event loop.
        try:
            payload = await asyncio.to_thread(self._decode, id_token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Invalid Google token.") from exc

        if payload.get("iss") not in _GOOGLE_ISSUERS:
            raise AuthenticationError("Invalid Google token.")

        return GoogleIdentity(
            sub=str(payload["sub"]),
            email=payload.get("email"),
            name=payload.get("name"),
        )

    def _decode(self, id_token: str) -> dict[str, Any]:
        signing_key = self._jwk_client.get_signing_key_from_jwt(id_token)
        decoded: dict[str, Any] = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self._client_id,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
        return decoded
