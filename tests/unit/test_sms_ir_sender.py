"""sms.ir OTP sender adapter, exercised through httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.exceptions import ExternalServiceError
from app.infrastructure.auth.sms_ir_otp_sender import SmsIrOTPSender

API_KEY = "test-api-key"
TEMPLATE_ID = 123456


def make_sender(handler: httpx.MockTransport) -> SmsIrOTPSender:
    return SmsIrOTPSender(api_key=API_KEY, template_id=TEMPLATE_ID, transport=handler)


def ok_response() -> httpx.Response:
    return httpx.Response(200, json={"status": 1, "message": "موفق"})


async def test_send_posts_verify_with_expected_payload() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok_response()

    sender = make_sender(httpx.MockTransport(handler))
    await sender.send("+989121234567", "123456")

    (request,) = seen
    assert request.url == "https://api.sms.ir/v1/send/verify"
    assert request.headers["x-api-key"] == API_KEY
    body = json.loads(request.content)
    assert body == {
        "mobile": "+989121234567",
        "templateId": TEMPLATE_ID,
        "parameters": [{"name": "CODE", "value": "123456"}],
    }


async def test_http_error_raises_external_service_error_without_leaking_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    sender = make_sender(httpx.MockTransport(handler))
    with pytest.raises(ExternalServiceError) as excinfo:
        await sender.send("+989121234567", "123456")

    assert excinfo.value.__cause__ is None
    assert API_KEY not in str(excinfo.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"status": 401, "message": "invalid api key"}),
        httpx.Response(200, json={"status": 0, "message": "invalid mobile"}),
        httpx.Response(200, content=b"not json"),
    ],
)
async def test_rejection_envelopes_raise_external_service_error(
    response: httpx.Response,
) -> None:
    sender = make_sender(httpx.MockTransport(lambda request: response))
    with pytest.raises(ExternalServiceError):
        await sender.send("+989121234567", "123456")


async def test_error_detail_is_not_exposed_to_clients() -> None:
    body = json.dumps({"status": 0, "message": "secret detail"})
    sender = make_sender(
        httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode()))
    )
    with pytest.raises(ExternalServiceError) as excinfo:
        await sender.send("+989121234567", "123456")
    assert "secret detail" not in str(excinfo.value)
