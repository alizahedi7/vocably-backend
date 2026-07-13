"""OTP sender backed by Kavenegar's Verify Lookup API (Iranian SMS provider).

Uses the template-based verify endpoint, which delivers transactional codes even to
numbers on the operators' ad-blocking blacklist. Selected via ``OTP_SENDER=kavenegar``.
"""

from __future__ import annotations

import httpx

from app.application.ports.otp_sender import OTPSender
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger("vocably.otp.kavenegar")

_BASE_URL = "https://api.kavenegar.com/v1"


class KavenegarOTPSender(OTPSender):
    def __init__(
        self,
        api_key: str,
        template: str,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._template = template
        self._timeout = timeout_seconds
        self._transport = transport

    async def send(self, phone: str, code: str) -> None:
        # The URL embeds the API key, so it must never appear in logs or exception
        # chains — failures are re-raised `from None` with a sanitized message.
        url = f"{_BASE_URL}/{self._api_key}/verify/lookup.json"
        params = {"receptor": phone, "token": code, "template": self._template}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            logger.error("Kavenegar request failed: %s", type(exc).__name__)
            raise ExternalServiceError("Failed to send verification SMS.") from None

        status = self._envelope_status(response)
        if response.status_code >= 400 or status != 200:
            logger.error(
                "Kavenegar rejected OTP delivery (http=%s, status=%s)",
                response.status_code,
                status,
            )
            raise ExternalServiceError("Failed to send verification SMS.") from None

    @staticmethod
    def _envelope_status(response: httpx.Response) -> int | None:
        """Extract ``return.status`` from Kavenegar's response envelope, if present."""
        try:
            status = response.json()["return"]["status"]
        except (ValueError, KeyError, TypeError):
            return None
        return status if isinstance(status, int) else None
