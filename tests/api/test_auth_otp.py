"""OTP sign-in flow: request a code, verify it, and use the issued tokens."""

from __future__ import annotations

from httpx import AsyncClient

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


async def test_verify_without_request_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/otp/verify", json={"phone": PHONE, "code": "123456"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_otp"


async def test_code_is_single_use(client: AsyncClient, otp_sender: RecordingOTPSender) -> None:
    code = await request_code(client, otp_sender, PHONE)
    first = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert first.status_code == 200

    replay = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert replay.status_code == 401
