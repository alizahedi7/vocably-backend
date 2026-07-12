"""Auth request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.schemas.user import UserOut

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
