"""Kavenegar OTP sender adapter, exercised through httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.exceptions import ExternalServiceError
from app.infrastructure.auth.kavenegar_otp_sender import KavenegarOTPSender

API_KEY = "test-api-key"
TEMPLATE = "vocably-otp"


def make_sender(handler: httpx.MockTransport) -> KavenegarOTPSender:
    return KavenegarOTPSender(api_key=API_KEY, template=TEMPLATE, transport=handler)


def ok_response() -> httpx.Response:
    return httpx.Response(200, json={"return": {"status": 200, "message": "تایید شد"}})


async def test_send_calls_verify_lookup_with_expected_params() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok_response()

    sender = make_sender(httpx.MockTransport(handler))
    await sender.send("+989121234567", "123456")

    (request,) = seen
    assert request.url.path == f"/v1/{API_KEY}/verify/lookup.json"
    assert request.url.params["receptor"] == "+989121234567"
    assert request.url.params["token"] == "123456"
    assert request.url.params["template"] == TEMPLATE


async def test_http_error_raises_external_service_error_without_leaking_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    sender = make_sender(httpx.MockTransport(handler))
    with pytest.raises(ExternalServiceError) as excinfo:
        await sender.send("+989121234567", "123456")

    assert excinfo.value.__cause__ is None  # chain suppressed: reprs embed the keyed URL
    assert API_KEY not in str(excinfo.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(418, json={"return": {"status": 418, "message": "invalid api key"}}),
        httpx.Response(200, json={"return": {"status": 411, "message": "invalid receptor"}}),
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
    body = json.dumps({"return": {"status": 407, "message": "secret detail"}})
    sender = make_sender(
        httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode()))
    )
    with pytest.raises(ExternalServiceError) as excinfo:
        await sender.send("+989121234567", "123456")
    assert "secret detail" not in str(excinfo.value)
