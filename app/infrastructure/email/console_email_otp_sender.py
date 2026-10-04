"""Email OTP senders for when there is no mail provider.

``ConsoleEmailOTPSender`` is the dev default and the twin of
``ConsoleOTPSender``. ``UnconfiguredEmailOTPSender`` is what production gets in
its place: logging the code there would answer "sent" for an email that never
leaves the building, and write a live credential into the logs while doing it.
"""

from __future__ import annotations

from app.application.ports.email_otp_sender import EmailOTPSender
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger("vocably.otp")


class ConsoleEmailOTPSender(EmailOTPSender):
    async def send(self, email: str, code: str) -> None:
        logger.info("✉️  OTP for %s is %s  (dev sender — not actually emailed)", email, code)


class UnconfiguredEmailOTPSender(EmailOTPSender):
    """Refuses, loudly, rather than pretending.

    Raised at send time and not at startup, deliberately unlike the guards in
    ``config.py``: a missing mail provider must cost the one feature that needs
    it, not the deploy that introduced it.
    """

    async def send(self, email: str, code: str) -> None:
        logger.error("Email OTP requested but EMAIL_SENDER is not configured for production.")
        raise ExternalServiceError("Email verification isn't available right now.")
