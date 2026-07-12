"""Authentication endpoints: phone/OTP, Google, token refresh."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AuthServiceDep
from app.api.v1.schemas.auth import (
    AuthOut,
    GoogleSignInIn,
    MessageOut,
    RefreshIn,
    RequestOTPIn,
    TokenPairOut,
    VerifyOTPIn,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/otp/request", response_model=MessageOut, status_code=status.HTTP_202_ACCEPTED)
async def request_otp(payload: RequestOTPIn, auth: AuthServiceDep) -> MessageOut:
    """Send a one-time passcode to the given phone number."""
    await auth.request_otp(payload.phone)
    return MessageOut(detail="Verification code sent.")


@router.post("/otp/verify", response_model=AuthOut)
async def verify_otp(payload: VerifyOTPIn, auth: AuthServiceDep) -> AuthOut:
    """Verify an OTP and sign in (creating the account if it's new)."""
    result = await auth.verify_otp(payload.phone, payload.code)
    return AuthOut.model_validate(result)


@router.post("/google", response_model=AuthOut)
async def sign_in_with_google(payload: GoogleSignInIn, auth: AuthServiceDep) -> AuthOut:
    result = await auth.sign_in_with_google(payload.id_token)
    return AuthOut.model_validate(result)


@router.post("/refresh", response_model=TokenPairOut)
async def refresh(payload: RefreshIn, auth: AuthServiceDep) -> TokenPairOut:
    tokens = await auth.refresh(payload.refresh_token)
    return TokenPairOut.model_validate(tokens)
