"""Profile endpoints: /users/me, onboarding, and partial updates."""

from __future__ import annotations

from httpx import AsyncClient

from app.infrastructure.db.models.user import UserModel
from tests.api.conftest import UserFactory, bearer


async def test_me_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_error"


async def test_me_returns_profile(
    client: AsyncClient, user: UserModel, auth_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(user.id)
    assert body["phone"] == user.phone
    assert body["name"] == "Ali"
    assert body["streak"] == 0


async def test_onboarding_completes_profile(
    client: AsyncClient, make_user: UserFactory
) -> None:
    user = await make_user(name="", onboarded=False)

    response = await client.post(
        "/api/v1/users/me/onboarding",
        headers=bearer(user.id),
        json={"name": "  Parisa ", "age_range": "25-34", "native_language": "Persian"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Parisa"
    assert body["age_range"] == "25-34"
    assert body["native_language"] == "Persian"
    assert body["onboarded"] is True


async def test_partial_profile_update(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    response = await client.patch(
        "/api/v1/users/me", headers=auth_headers, json={"app_language": "Persian"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["app_language"] == "Persian"
    assert body["name"] == "Ali"  # untouched
