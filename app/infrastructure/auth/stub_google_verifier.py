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


class StubGoogleVerifier(GoogleVerifier):
    async def verify(self, id_token: str) -> GoogleIdentity:
        token = id_token.strip()
        if not token:
            raise AuthenticationError("Missing Google token.")

        parts = token.split(":")
        sub = parts[0]
        email = parts[1] if len(parts) > 1 else f"{sub}@example.com"
        name = parts[2] if len(parts) > 2 else "Google User"
        return GoogleIdentity(sub=f"google-{sub}", email=email, name=name)
