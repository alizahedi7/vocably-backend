"""Dev Google verifier that trusts a fake token payload.

Accepts either:
  * a plain string used directly as the Google subject, or
  * a ``sub:email:name`` triple to control the returned identity in tests.

In production swap for an adapter that validates the id_token against Google's JWKS and
checks the audience (``GOOGLE_CLIENT_ID``).
"""

from __future__ import annotations

from app.application.ports.google_verifier import GoogleIdentity, GoogleVerifier
from app.core.exceptions import AuthenticationError

# A real Google id_token is a JWT (three base64url segments joined by ".") and
# runs to hundreds of characters. The stub uses the token verbatim as the
# subject/email, so a real token here means the app is misconfigured
# (GOOGLE_VERIFIER=stub while a client sends genuine Google tokens) — fail
# loudly instead of manufacturing an oversized identity that overflows the
# users.email / users.google_sub columns.
_MAX_STUB_TOKEN_LENGTH = 128


class StubGoogleVerifier(GoogleVerifier):
    async def verify(self, id_token: str) -> GoogleIdentity:
        token = id_token.strip()
        if not token:
            raise AuthenticationError("Missing Google token.")

        if len(token) > _MAX_STUB_TOKEN_LENGTH or token.count(".") == 2:
            raise AuthenticationError(
                "Received what looks like a real Google id_token, but the stub "
                "verifier is active. Set GOOGLE_VERIFIER=google (and GOOGLE_CLIENT_ID)."
            )

        parts = token.split(":")
        sub = parts[0]
        email = parts[1] if len(parts) > 1 else f"{sub}@example.com"
        name = parts[2] if len(parts) > 2 else "Google User"
        return GoogleIdentity(sub=f"google-{sub}", email=email, name=name)
