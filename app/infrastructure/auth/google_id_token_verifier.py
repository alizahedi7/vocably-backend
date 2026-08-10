"""Google verifier that validates id_tokens via Google's tokeninfo endpoint.

Selected via ``GOOGLE_VERIFIER=google``; requires ``GOOGLE_CLIENT_ID`` (the token
audience). Google's own signature/expiry checks happen server-side on their end —
this trades a network round-trip for not needing local JWKS key material, because
Google's JWKS certs endpoint (googleapis.com/oauth2/v3/certs) is geo-blocked from
some hosting regions while tokeninfo (oauth2.googleapis.com) is not.
"""

from __future__ import annotations

import httpx

from app.application.ports.google_verifier import GoogleIdentity, GoogleVerifier
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger

logger = get_logger("vocably.auth.google")

_GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
_GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleIdTokenVerifier(GoogleVerifier):
    def __init__(
        self,
        client_id: str,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._timeout = timeout_seconds
        self._transport = transport

    async def verify(self, id_token: str) -> GoogleIdentity:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(
                    _GOOGLE_TOKENINFO_URL, params={"id_token": id_token}
                )
        except httpx.HTTPError as exc:
            logger.error("Google tokeninfo request failed: %s", type(exc).__name__)
            raise AuthenticationError("Invalid Google token.") from None

        if response.status_code != 200:
            raise AuthenticationError("Invalid Google token.")

        try:
            payload = response.json()
        except ValueError:
            raise AuthenticationError("Invalid Google token.") from None

        # tokeninfo validates the signature and expiry; audience and issuer are
        # ours to check — it will happily decode a token minted for someone else.
        if payload.get("aud") != self._client_id:
            raise AuthenticationError("Invalid Google token.")
        if payload.get("iss") not in _GOOGLE_ISSUERS:
            raise AuthenticationError("Invalid Google token.")

        sub = payload.get("sub")
        if not sub:
            raise AuthenticationError("Invalid Google token.")

        return GoogleIdentity(
            sub=str(sub),
            email=payload.get("email"),
            name=payload.get("name"),
        )
