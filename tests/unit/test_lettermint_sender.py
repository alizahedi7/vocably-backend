"""LetterMint email OTP sender, exercised through httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.exceptions import ExternalServiceError
from app.infrastructure.email.console_email_otp_sender import UnconfiguredEmailOTPSender
from app.infrastructure.email.lettermint_email_otp_sender import LetterMintEmailOTPSender
from app.infrastructure.email.templates import link_code_email

API_TOKEN = "lm_test_token"
SENDER = "Vocably <no-reply@vocably.test>"


def make_sender(handler: httpx.MockTransport, route: str = "") -> LetterMintEmailOTPSender:
    return LetterMintEmailOTPSender(
        api_token=API_TOKEN, sender=SENDER, ttl_minutes=5, route=route, transport=handler
    )


def accepted() -> httpx.Response:
    return httpx.Response(202, json={"message_id": "m-1", "status": "pending"})


async def test_send_posts_the_message_with_the_project_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return accepted()

    await make_sender(httpx.MockTransport(handler)).send("ali@example.com", "123456")

    (request,) = seen
    assert request.method == "POST"
    assert request.url == "https://api.lettermint.co/v1/send"
    assert request.headers["x-lettermint-token"] == API_TOKEN
    body = json.loads(request.content)
    assert body["from"] == SENDER
    assert body["to"] == ["ali@example.com"]
    assert body["subject"] == "Your Vocably verification code"
    assert "123456" in body["html"]
    assert "123456" in body["text"]
    # No route unless one is configured: the project's default applies.
    assert "route" not in body


async def test_a_configured_route_is_sent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return accepted()

    await make_sender(httpx.MockTransport(handler), route="transactional").send(
        "ali@example.com", "123456"
    )

    assert json.loads(seen[0].content)["route"] == "transactional"


async def test_http_error_raises_external_service_error_without_leaking_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    with pytest.raises(ExternalServiceError) as excinfo:
        await make_sender(httpx.MockTransport(handler)).send("ali@example.com", "123456")

    assert excinfo.value.__cause__ is None
    assert API_TOKEN not in str(excinfo.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"message": "Unauthenticated."}),
        httpx.Response(422, json={"message": "The from domain is not verified."}),
        httpx.Response(429, json={"message": "Too many requests."}),
        httpx.Response(500, content=b"oops"),
        # Anything but "accepted for delivery" is a failure, a plain 200 included.
        httpx.Response(200, json={"status": "pending"}),
    ],
)
async def test_anything_but_accepted_raises_external_service_error(
    response: httpx.Response,
) -> None:
    sender = make_sender(httpx.MockTransport(lambda request: response))
    with pytest.raises(ExternalServiceError):
        await sender.send("ali@example.com", "123456")


async def test_the_unconfigured_sender_refuses_rather_than_pretending() -> None:
    with pytest.raises(ExternalServiceError):
        await UnconfiguredEmailOTPSender().send("ali@example.com", "123456")


def test_the_email_carries_the_code_its_lifetime_and_the_disclaimer() -> None:
    message = link_code_email("482913", ttl_minutes=5)

    # Not in the subject: that is what a lock screen shows.
    assert "482913" not in message.subject
    for body in (message.html, message.text):
        assert "482913" in body
        assert "5 minutes" in body
        assert "ignore this email" in body
        assert "never ask you for this code" in body
    # Self-contained: a remote image or stylesheet is a code some clients hide.
    assert "<img" not in message.html
    assert "<link" not in message.html


def test_one_minute_is_singular() -> None:
    assert "1 minute." in link_code_email("482913", ttl_minutes=1).text
