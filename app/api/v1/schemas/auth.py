"""Auth request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.schemas.user import UserOut
from app.application.dto import LinkCodeIssued
from app.domain.enums import IdentifierType
from app.domain.services.identifiers import MAX_EMAIL_LENGTH

# E.164: leading +, non-zero first digit, 8..15 digits total.
_E164_PATTERN = r"^\+[1-9]\d{7,14}$"


class RequestOTPIn(BaseModel):
    phone: str = Field(pattern=_E164_PATTERN, examples=["+989121234567"])


class VerifyOTPIn(BaseModel):
    phone: str = Field(pattern=_E164_PATTERN, examples=["+989121234567"])
    code: str = Field(min_length=4, max_length=8, examples=["123456"])


class GoogleSignInIn(BaseModel):
    id_token: str = Field(min_length=1, description="Google OAuth id_token")


class RefreshIn(BaseModel):
    refresh_token: str


class TokenPairOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user: UserOut
    tokens: TokenPairOut
    is_new_user: bool


class MessageOut(BaseModel):
    detail: str


class LinkRequestOTPIn(BaseModel):
    """Ask for a code to prove a phone number or an email address.

    ``target`` is free text on purpose and normalised server-side — a phone
    with spaces, an address with capitals — so the client need not reproduce
    the rules, and cannot get them subtly different.
    """

    type: IdentifierType
    target: str = Field(
        min_length=3,
        max_length=MAX_EMAIL_LENGTH,
        examples=["ali@example.com", "+989121234567"],
    )


class LinkVerifyOTPIn(LinkRequestOTPIn):
    code: str = Field(min_length=4, max_length=8, examples=["123456"])


class LinkCodeSentOut(BaseModel):
    detail: str
    type: IdentifierType
    #: The normalised form the code went to — show this, and send it back.
    target: str
    #: Seconds until the code expires, and until another may be requested.
    expires_in: int
    resend_after: int

    @classmethod
    def from_issued(cls, issued: LinkCodeIssued) -> LinkCodeSentOut:
        return cls(
            detail="Verification code sent.",
            type=issued.kind,
            target=issued.target,
            expires_in=issued.expires_in_seconds,
            resend_after=issued.resend_after_seconds,
        )
