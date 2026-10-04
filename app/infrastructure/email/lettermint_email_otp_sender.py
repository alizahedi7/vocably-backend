"""Email OTP sender backed by LetterMint's Sending API.

The HTTP API rather than SMTP: ``httpx`` is already how every other provider in
this codebase is reached, and SMTP would add a dependency to say less — the API
answers with a status per message. Selected via ``EMAIL_SENDER=lettermint``.

``EMAIL_FROM`` must be an address on a domain verified in the LetterMint
project the token belongs to; anything else is refused with a 422.
"""

from __future__ import annotations

import httpx

from app.application.ports.email_otp_sender import EmailOTPSender
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.infrastructure.email.templates import link_code_email

logger = get_logger("vocably.otp.lettermint")

_SEND_URL = "https://api.lettermint.co/v1/send"


class LetterMintEmailOTPSender(EmailOTPSender):
    def __init__(
        self,
        api_token: str,
        sender: str,
        ttl_minutes: int,
        route: str = "",
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_token = api_token
        self._sender = sender
        self._ttl_minutes = ttl_minutes
        self._route = route
        self._timeout = timeout_seconds
        self._transport = transport

    async def send(self, email: str, code: str) -> None:
        message = link_code_email(code, self._ttl_minutes)
        payload: dict[str, object] = {
            "from": self._sender,
            "to": [email],
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
        }
        if self._route:
            payload["route"] = self._route
        headers = {"x-lettermint-token": self._api_token, "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(_SEND_URL, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.error("LetterMint request failed: %s", type(exc).__name__)
            raise ExternalServiceError("Failed to send verification email.") from None

        # 202 with a message id is the only success. The body is not logged on
        # failure: a validation error echoes the recipient's address back.
        if response.status_code != 202:
            logger.error("LetterMint rejected OTP delivery (http=%s)", response.status_code)
            raise ExternalServiceError("Failed to send verification email.") from None
