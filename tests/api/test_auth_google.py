"""Google sign-in (through the dev stub verifier, which trusts sub:email:name tokens)."""

from __future__ import annotations

from httpx import AsyncClient


async def test_google_sign_in_creates_user(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/google", json={"id_token": "abc123:ali@example.com:Ali"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_new_user"] is True
    assert body["user"]["auth_method"] == "google"
    assert body["user"]["email"] == "ali@example.com"
    assert body["user"]["name"] == "Ali"


async def test_google_sign_in_reuses_existing_account(client: AsyncClient) -> None:
    first = await client.post(
        "/api/v1/auth/google", json={"id_token": "abc123:ali@example.com:Ali"}
    )
    second = await client.post(
        "/api/v1/auth/google", json={"id_token": "abc123:ali@example.com:Ali"}
    )
    assert second.status_code == 200
    assert second.json()["is_new_user"] is False
    assert second.json()["user"]["id"] == first.json()["user"]["id"]
