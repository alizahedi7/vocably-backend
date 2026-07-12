"""Dev OTP sender that logs the code instead of sending an SMS.

In production swap this for a Twilio/SNS adapter implementing the same ``OTPSender`` port.
"""

from __future__ import annotations

from app.application.ports.otp_sender import OTPSender
from app.core.logging import get_logger

logger = get_logger("vocably.otp")


class ConsoleOTPSender(OTPSender):
    async def send(self, phone: str, code: str) -> None:
        logger.info("📱  OTP for %s is %s  (dev sender — not actually texted)", phone, code)
