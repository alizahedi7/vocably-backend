"""Security primitives: JWT issuance/verification and OTP hashing.

Kept free of any web-framework imports so it can be reused anywhere.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

import jwt

from app.core.config import settings
from app.core.exceptions import InvalidTokenError


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


def _create_token(subject: str, token_type: TokenType, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type.value,
        "iat": now,
        "exp": now + expires_delta,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str | UUID) -> str:
    return _create_token(
        str(subject),
        TokenType.ACCESS,
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(subject: str | UUID) -> str:
    return _create_token(
        str(subject),
        TokenType.REFRESH,
        timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str, expected_type: TokenType | None = None) -> dict[str, Any]:
    """Decode and validate a JWT, raising ``InvalidTokenError`` on any problem."""
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:  # expired, bad signature, malformed, ...
        raise InvalidTokenError() from exc

    if expected_type is not None and payload.get("type") != expected_type.value:
        raise InvalidTokenError("Unexpected token type.")
    return payload


# ── OTP hashing ──────────────────────────────────────────────
# OTP codes are never stored in plaintext. We keep a keyed hash (HMAC-SHA256 with the app
# secret as pepper) and compare in constant time.


def hash_otp(code: str) -> str:
    return hmac.new(settings.secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()


def verify_otp(code: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_otp(code), hashed)


def generate_otp(length: int | None = None) -> str:
    """Generate a numeric OTP of the configured length.

    Returns ``settings.otp_fixed_code`` verbatim when set — a dev/test override
    (validated at settings load to never apply in production) so mobile/QA can
    sign in without reading server logs for the code.
    """
    if settings.otp_fixed_code:
        return settings.otp_fixed_code
    n = length or settings.otp_length
    return "".join(secrets.choice("0123456789") for _ in range(n))
