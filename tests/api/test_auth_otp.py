"""OTP sign-in flow: request a code, verify it, and use the issued tokens."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.domain.entities.otp_challenge import MAX_OTP_ATTEMPTS
from tests.api.conftest import RecordingOTPSender

PHONE = "+989121234567"


async def request_code(client: AsyncClient, otp_sender: RecordingOTPSender, phone: str) -> str:
    response = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    assert response.status_code == 202, response.text
    return otp_sender.last_code_for(phone)


async def test_full_otp_flow_creates_user_and_signs_in(
    client: AsyncClient, otp_sender: RecordingOTPSender
) -> None:
    code = await request_code(client, otp_sender, PHONE)

    response = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_new_user"] is True
    assert body["user"]["phone"] == PHONE
    assert body["user"]["auth_method"] == "phone"
    assert body["tokens"]["token_type"] == "bearer"

    me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {body['tokens']['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["id"] == body["user"]["id"]


async def test_verify_existing_user_is_not_new(
    client: AsyncClient, otp_sender: RecordingOTPSender
) -> None:
    code = await request_code(client, otp_sender, PHONE)
    first = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert first.json()["is_new_user"] is True

    code = await request_code(client, otp_sender, PHONE)
    second = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert second.status_code == 200
    assert second.json()["is_new_user"] is False
    assert second.json()["user"]["id"] == first.json()["user"]["id"]


async def test_verify_with_wrong_code_is_rejected(
    client: AsyncClient, otp_sender: RecordingOTPSender
) -> None:
    code = await request_code(client, otp_sender, PHONE)
    wrong = "000000" if code != "000000" else "111111"

    response = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": wrong})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_otp"


async def test_expired_code_is_rejected(
    client: AsyncClient, otp_sender: RecordingOTPSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "otp_ttl_seconds", 0)
    code = await request_code(client, otp_sender, PHONE)

    response = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_otp"


async def test_code_is_locked_after_max_wrong_attempts(
    client: AsyncClient, otp_sender: RecordingOTPSender
) -> None:
    code = await request_code(client, otp_sender, PHONE)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(MAX_OTP_ATTEMPTS):
        attempt = await client.post(
            "/api/v1/auth/otp/verify", json={"phone": PHONE, "code": wrong}
        )
        assert attempt.status_code == 401

    # The challenge is exhausted: even the correct code no longer signs in.
    response = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_otp"


async def test_verify_without_request_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/otp/verify", json={"phone": PHONE, "code": "123456"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_otp"


async def test_resend_within_cooldown_is_rate_limited(
    client: AsyncClient, otp_sender: RecordingOTPSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "otp_resend_cooldown_seconds", 30)
    code = await request_code(client, otp_sender, PHONE)

    retry = await client.post("/api/v1/auth/otp/request", json={"phone": PHONE})
    assert retry.status_code == 429
    assert retry.json()["error"]["code"] == "rate_limited"

    # The rejected resend must not invalidate the code already sent.
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert verify.status_code == 200


async def test_consumed_challenge_does_not_block_new_request(
    client: AsyncClient, otp_sender: RecordingOTPSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "otp_resend_cooldown_seconds", 30)
    code = await request_code(client, otp_sender, PHONE)
    verified = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert verified.status_code == 200

    # Signing in consumed the challenge, so a fresh request is allowed immediately.
    response = await client.post("/api/v1/auth/otp/request", json={"phone": PHONE})
    assert response.status_code == 202


async def test_code_is_single_use(client: AsyncClient, otp_sender: RecordingOTPSender) -> None:
    code = await request_code(client, otp_sender, PHONE)
    first = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert first.status_code == 200

    replay = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert replay.status_code == 401


@pytest.mark.parametrize(
    "phone",
    [
        "5551234567",  # missing +
        "09121234567",  # national format, no country code
        "+0912123456",  # leading zero after +
        "+98912",  # too short
        "+9891212345678901",  # too long
        "+98 912 123 4567",  # spaces
    ],
)
async def test_malformed_phone_numbers_are_rejected(client: AsyncClient, phone: str) -> None:
    response = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    assert response.status_code == 422
