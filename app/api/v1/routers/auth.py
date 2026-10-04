"""Authentication endpoints: phone/OTP, Google, token refresh, and linking."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import (
    AccountLinkServiceDep,
    AuthServiceDep,
    CurrentUser,
    SessionDep,
    UserServiceDep,
    enforce_otp_request_ip_limit,
)
from app.api.v1.schemas.auth import (
    AuthOut,
    GoogleSignInIn,
    LinkCodeSentOut,
    LinkRequestOTPIn,
    LinkVerifyOTPIn,
    MessageOut,
    RefreshIn,
    RequestOTPIn,
    TokenPairOut,
    VerifyOTPIn,
)
from app.api.v1.schemas.user import UserOut
from app.core.exceptions import InvalidLinkCodeError, InvalidOTPError
from app.domain.enums import IdentifierType

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/otp/request",
    response_model=MessageOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(enforce_otp_request_ip_limit)],
)
async def request_otp(payload: RequestOTPIn, auth: AuthServiceDep) -> MessageOut:
    """Send a one-time passcode to the given phone number."""
    await auth.request_otp(payload.phone)
    return MessageOut(detail="Verification code sent.")


@router.post("/otp/verify", response_model=AuthOut)
async def verify_otp(payload: VerifyOTPIn, auth: AuthServiceDep, session: SessionDep) -> AuthOut:
    """Verify an OTP and sign in (creating the account if it's new)."""
    try:
        result = await auth.verify_otp(payload.phone, payload.code)
    except InvalidOTPError:
        # The request session rolls back when a handler raises; the failed-attempt
        # counter must survive anyway or the brute-force lockout never engages.
        await session.commit()
        raise
    return AuthOut.model_validate(result)


@router.post("/google", response_model=AuthOut)
async def sign_in_with_google(payload: GoogleSignInIn, auth: AuthServiceDep) -> AuthOut:
    result = await auth.sign_in_with_google(payload.id_token)
    return AuthOut.model_validate(result)


@router.post("/refresh", response_model=TokenPairOut)
async def refresh(payload: RefreshIn, auth: AuthServiceDep) -> TokenPairOut:
    tokens = await auth.refresh(payload.refresh_token)
    return TokenPairOut.model_validate(tokens)


# ── Linking a second identifier ──────────────────────────────
# All three act on the caller's own account and answer the account as it now
# stands, settled like ``GET /users/me``: the client keeps whichever user
# object arrived last, and an unsettled streak here would overwrite a settled
# one. Tokens are not reissued — the user id, which is all they carry, is the
# one thing linking never changes.


@router.post(
    "/link/request-otp",
    response_model=LinkCodeSentOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_link_otp(
    payload: LinkRequestOTPIn,
    current_user: CurrentUser,
    links: AccountLinkServiceDep,
) -> LinkCodeSentOut:
    """Send a code to a phone number or email, to add it to this account.

    **409** ``identifier_already_in_use`` when another account has already
    verified it; **429** past one request a minute or five an hour.
    """
    issued = await links.request_code(current_user, payload.type, payload.target)
    return LinkCodeSentOut.from_issued(issued)


@router.post("/link/verify-otp", response_model=UserOut)
async def verify_link_otp(
    payload: LinkVerifyOTPIn,
    current_user: CurrentUser,
    links: AccountLinkServiceDep,
    users: UserServiceDep,
    session: SessionDep,
) -> UserOut:
    """Check the code and attach the identifier. Signing in with it afterwards
    opens this same account.

    A wrong code is **400** ``invalid_otp`` — not 401, which on a route that
    carries a bearer token would read as an expired session.
    """
    try:
        user = await links.verify_code(current_user, payload.type, payload.target, payload.code)
    except InvalidLinkCodeError:
        # As in ``verify_otp`` above: the session rolls back when a handler
        # raises, and the spent attempt must survive or the cap never engages.
        await session.commit()
        raise
    return UserOut.model_validate(await users.settled(user))


@router.delete("/link/{identifier_type}", response_model=UserOut)
async def unlink_identifier(
    identifier_type: IdentifierType,
    current_user: CurrentUser,
    links: AccountLinkServiceDep,
    users: UserServiceDep,
) -> UserOut:
    """Take the phone or the email off this account.

    Removing the email also disconnects Google sign-in. **409**
    ``last_sign_in_method`` when it would leave no way to sign in.
    """
    user = await links.unlink(current_user, identifier_type)
    return UserOut.model_validate(await users.settled(user))
