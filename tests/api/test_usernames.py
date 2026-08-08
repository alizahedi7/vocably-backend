"""Handles: claiming one, checking availability, and the profile fields beside it."""

from __future__ import annotations

from httpx import AsyncClient

from tests.api.conftest import UserFactory, bearer


async def test_onboarding_claims_a_handle_and_the_learning_profile(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/users/me/onboarding",
        headers=auth_headers,
        json={
            "name": "Ali Zahedi",
            "username": "ali_z",
            "native_language": "Persian",
            "target_language": "English",
            "proficiency": "intermediate",
            "study_time": "evening",
            "timezone": "Asia/Tehran",
            "daily_goal": 15,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Exactly the snake_case keys lib/models/user_profile.dart parses.
    assert body["username"] == "ali_z"
    assert body["target_language"] == "English"
    assert body["proficiency"] == "intermediate"
    assert body["study_time"] == "evening"
    assert body["timezone"] == "Asia/Tehran"
    assert body["onboarded"] is True

    me = await client.get("/api/v1/users/me", headers=auth_headers)
    assert me.json()["username"] == "ali_z"


async def test_a_handle_is_stored_lowercased(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # Two casings of one handle must not both be claimable, so normalising is
    # the server's job rather than the client's.
    response = await client.patch(
        "/api/v1/users/me", headers=auth_headers, json={"username": "  Ali_Z  "}
    )
    assert response.status_code == 200, response.text
    assert response.json()["username"] == "ali_z"


async def test_a_taken_handle_is_refused_with_copy_the_user_can_read(
    client: AsyncClient, auth_headers: dict[str, str], make_user: UserFactory
) -> None:
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"username": "parisa"})
    other = await make_user(phone="+989121110020")

    response = await client.patch(
        "/api/v1/users/me", headers=bearer(other.id), json={"username": "Parisa"}
    )
    assert response.status_code == 409
    # The client shows `detail` verbatim, so 4xx messages are user-facing copy.
    assert response.json()["detail"] == "That handle is already taken."


async def test_malformed_handles_are_refused(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    for handle in ("ab", "1ali", "ali z", "ali-z"):
        response = await client.patch(
            "/api/v1/users/me", headers=auth_headers, json={"username": handle}
        )
        assert response.status_code == 422, f"{handle} should be refused"
        assert "3–20 characters" in response.json()["detail"]


async def test_reserved_handles_cannot_be_claimed(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # `/join/<code>` and `/users/me` are real paths.
    for handle in ("admin", "vocably", "support", "join", "me"):
        response = await client.patch(
            "/api/v1/users/me", headers=auth_headers, json={"username": handle}
        )
        assert response.status_code == 422, f"{handle} should be reserved"


async def test_username_available_answers_for_free_taken_and_unusable_handles(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    free = await client.get(
        "/api/v1/users/username-available", headers=auth_headers, params={"username": "newcomer"}
    )
    assert free.status_code == 200
    assert free.json() == {"available": True}

    await client.patch("/api/v1/users/me", headers=auth_headers, json={"username": "newcomer"})
    taken = await client.get(
        "/api/v1/users/username-available", headers=auth_headers, params={"username": "newcomer"}
    )
    assert taken.json() == {"available": False}

    # Malformed and reserved answer false rather than erroring: the client
    # treats a failure as "unknown" and lets the user through, which must not
    # be reachable by typing.
    for handle in ("ab", "Ali Z", "admin"):
        response = await client.get(
            "/api/v1/users/username-available", headers=auth_headers, params={"username": handle}
        )
        assert response.status_code == 200, handle
        assert response.json() == {"available": False}, handle


async def test_username_available_requires_authentication(client: AsyncClient) -> None:
    # Otherwise it is an unauthenticated handle-enumeration oracle.
    response = await client.get("/api/v1/users/username-available", params={"username": "someone"})
    assert response.status_code == 401


async def test_keeping_your_own_handle_is_not_a_conflict(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.patch("/api/v1/users/me", headers=auth_headers, json={"username": "ali_z"})
    again = await client.patch(
        "/api/v1/users/me", headers=auth_headers, json={"username": "ali_z", "name": "Ali"}
    )
    assert again.status_code == 200
    assert again.json()["username"] == "ali_z"


async def test_an_unknown_timezone_is_refused_at_the_write(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.patch(
        "/api/v1/users/me", headers=auth_headers, json={"timezone": "Mars/Olympus"}
    )
    assert response.status_code == 422
    assert "not a known timezone" in response.json()["detail"]


async def test_profile_fields_default_to_null_for_an_account_that_never_set_them(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # The client parses all of these as nullable; a missing key would crash it.
    body = (await client.get("/api/v1/users/me", headers=auth_headers)).json()
    for key in ("username", "target_language", "proficiency", "study_time", "timezone"):
        assert key in body
        assert body[key] is None
