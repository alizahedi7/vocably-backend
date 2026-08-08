"""Application-level behavior: health probe and error-envelope guarantees."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.api.deps import get_otp_sender
from app.application.ports.otp_sender import OTPSender
from app.core.exceptions import AppError
from app.main import app

PHONE = "+989121234567"


class _FailingOTPSender(OTPSender):
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def send(self, phone: str, code: str) -> None:
        raise self._exc


async def test_health_probe(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_unmapped_app_error_returns_400_envelope(client: AsyncClient) -> None:
    class OddError(AppError):
        code = "odd"

    app.dependency_overrides[get_otp_sender] = lambda: _FailingOTPSender(OddError("odd failure"))

    response = await client.post("/api/v1/auth/otp/request", json={"phone": PHONE})
    assert response.status_code == 400
    assert response.json() == {
        "error": {"code": "odd", "message": "odd failure"},
        "detail": "odd failure",
    }


async def test_error_envelope_carries_a_detail_key(client: AsyncClient) -> None:
    """The Flutter client reads ``detail``; vocably-admin reads ``error.code``.

    Both keys carry the same message so a 4xx written as user-facing copy
    actually reaches the user instead of rendering as "Request failed (404)".
    """
    app.dependency_overrides.pop(get_otp_sender, None)

    response = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": "000000"})

    assert response.status_code >= 400
    body = response.json()
    assert body["detail"] == body["error"]["message"]
    assert body["detail"]


async def test_unexpected_errors_do_not_leak_details(client: AsyncClient) -> None:
    secret = "kavenegar-key-abc123"
    app.dependency_overrides[get_otp_sender] = lambda: _FailingOTPSender(RuntimeError(secret))

    # A separate transport that renders unhandled crashes the way production does
    # instead of re-raising them into the test.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.post("/api/v1/auth/otp/request", json={"phone": PHONE})

    assert response.status_code == 500
    assert secret not in response.text
