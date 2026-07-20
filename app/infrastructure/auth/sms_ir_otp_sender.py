"""OTP sender backed by sms.ir's Verify API (Iranian SMS provider).

Uses the templated verify endpoint: a template is pre-registered in the sms.ir panel
with a single ``CODE`` parameter placeholder, and this adapter fills it in per request.
Selected via ``OTP_SENDER=sms_ir``.
"""

from __future__ import annotations

import httpx

from app.application.ports.otp_sender import OTPSender
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger("vocably.otp.sms_ir")

_VERIFY_URL = "https://api.sms.ir/v1/send/verify"


class SmsIrOTPSender(OTPSender):
    def __init__(
        self,
        api_key: str,
        template_id: int,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._template_id = template_id
        self._timeout = timeout_seconds
        self._transport = transport

    async def send(self, phone: str, code: str) -> None:
        headers = {
            "X-API-KEY": self._api_key,
            "Accept": "text/plain",
            "Content-Type": "application/json",
        }
        payload = {
            "mobile": phone,
            "templateId": self._template_id,
            "parameters": [{"name": "CODE", "value": code}],
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(_VERIFY_URL, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.error("sms.ir request failed: %s", type(exc).__name__)
            raise ExternalServiceError("Failed to send verification SMS.") from None

        status = self._envelope_status(response)
        if response.status_code >= 400 or status != 1:
            logger.error(
                "sms.ir rejected OTP delivery (http=%s, status=%s)",
                response.status_code,
                status,
            )
            raise ExternalServiceError("Failed to send verification SMS.") from None

    @staticmethod
    def _envelope_status(response: httpx.Response) -> int | None:
        """Extract the top-level ``status`` field from sms.ir's response envelope."""
        try:
            status = response.json()["status"]
        except (ValueError, KeyError, TypeError):
            return None
        return status if isinstance(status, int) else None
