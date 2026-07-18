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


async def test_onboarding_completes_profile(client: AsyncClient, make_user: UserFactory) -> None:
    user = await make_user(name="", onboarded=False)

    response = await client.post(
        "/api/v1/users/me/onboarding",
        headers=bearer(user.id),
        json={"name": "  Parisa ", "age_range": "25–34", "native_language": "Persian"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Parisa"
    assert body["age_range"] == "25–34"
    assert body["native_language"] == "Persian"
    assert body["onboarded"] is True


async def test_onboarding_accepts_all_app_age_options(
    client: AsyncClient, make_user: UserFactory
) -> None:
    # The two additions beyond the numeric ranges; both exceed the old 16-char column.
    for phone, age_range in (
        ("+989121110001", "Under 13"),
        ("+989121110002", "Prefer not to share"),
    ):
        user = await make_user(phone=phone, name="", onboarded=False)
        response = await client.post(
            "/api/v1/users/me/onboarding",
            headers=bearer(user.id),
            json={"name": "Kid", "age_range": age_range, "native_language": "English"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["age_range"] == age_range


async def test_me_serializes_default_interests_and_daily_goal(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["interests"] == []
    assert body["daily_goal"] == 10


async def test_onboarding_accepts_interests_and_daily_goal(
    client: AsyncClient, make_user: UserFactory
) -> None:
    user = await make_user(name="", onboarded=False)
    response = await client.post(
        "/api/v1/users/me/onboarding",
        headers=bearer(user.id),
        json={
            "name": "Parisa",
            "age_range": "25–34",
            "native_language": "Persian",
            "interests": ["travel", "food", "tech"],
            "daily_goal": 15,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["interests"] == ["travel", "food", "tech"]
    assert body["daily_goal"] == 15


async def test_onboarding_rejects_unknown_interest(
    client: AsyncClient, make_user: UserFactory
) -> None:
    user = await make_user(name="", onboarded=False)
    response = await client.post(
        "/api/v1/users/me/onboarding",
        headers=bearer(user.id),
        json={"name": "Parisa", "native_language": "Persian", "interests": ["astrology"]},
    )
    assert response.status_code == 422


async def test_onboarding_rejects_disallowed_daily_goal(
    client: AsyncClient, make_user: UserFactory
) -> None:
    user = await make_user(name="", onboarded=False)
    response = await client.post(
        "/api/v1/users/me/onboarding",
        headers=bearer(user.id),
        json={"name": "Parisa", "native_language": "Persian", "daily_goal": 12},
    )
    assert response.status_code == 422


async def test_onboarding_rejects_unknown_age_range(
    client: AsyncClient, make_user: UserFactory
) -> None:
    user = await make_user(name="", onboarded=False)
    response = await client.post(
        "/api/v1/users/me/onboarding",
        headers=bearer(user.id),
        json={"name": "Parisa", "age_range": "25-34", "native_language": "Persian"},
    )
    assert response.status_code == 422


async def test_partial_profile_update(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    response = await client.patch(
        "/api/v1/users/me", headers=auth_headers, json={"app_language": "Persian"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["app_language"] == "Persian"
    assert body["name"] == "Ali"  # untouched


async def test_patch_interests_alone_leaves_daily_goal_untouched(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    goal = await client.patch("/api/v1/users/me", headers=auth_headers, json={"daily_goal": 20})
    assert goal.status_code == 200

    response = await client.patch(
        "/api/v1/users/me", headers=auth_headers, json={"interests": ["music", "gaming"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["interests"] == ["music", "gaming"]
    assert body["daily_goal"] == 20  # untouched
    assert body["name"] == "Ali"  # untouched


async def test_patch_daily_goal_alone_leaves_interests_untouched(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    interests = await client.patch(
        "/api/v1/users/me", headers=auth_headers, json={"interests": ["sports"]}
    )
    assert interests.status_code == 200

    response = await client.patch("/api/v1/users/me", headers=auth_headers, json={"daily_goal": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["daily_goal"] == 5
    assert body["interests"] == ["sports"]  # untouched


async def test_patch_rejects_unknown_interest_and_bad_goal(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    bad_interest = await client.patch(
        "/api/v1/users/me", headers=auth_headers, json={"interests": ["cooking"]}
    )
    assert bad_interest.status_code == 422

    bad_goal = await client.patch("/api/v1/users/me", headers=auth_headers, json={"daily_goal": 25})
    assert bad_goal.status_code == 422
