"""Token refresh endpoint."""

from __future__ import annotations

from httpx import AsyncClient

from tests.api.conftest import RecordingOTPSender

PHONE = "+989121234567"


async def sign_in(client: AsyncClient, otp_sender: RecordingOTPSender) -> dict[str, str]:
    await client.post("/api/v1/auth/otp/request", json={"phone": PHONE})
    code = otp_sender.last_code_for(PHONE)
    response = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "code": code})
    assert response.status_code == 200
    tokens: dict[str, str] = response.json()["tokens"]
    return tokens


async def test_refresh_issues_new_token_pair(
    client: AsyncClient, otp_sender: RecordingOTPSender
) -> None:
    tokens = await sign_in(client, otp_sender)

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]

    me = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200


async def test_access_token_is_rejected_as_refresh_token(
    client: AsyncClient, otp_sender: RecordingOTPSender
) -> None:
    tokens = await sign_in(client, otp_sender)

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_garbage_refresh_token_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-jwt"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
