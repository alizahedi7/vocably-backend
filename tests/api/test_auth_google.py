"""Google sign-in (through the dev stub verifier, which trusts sub:email:name tokens)."""

from __future__ import annotations

from httpx import AsyncClient

from tests.api.conftest import UserFactory


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


# ── One account, whichever way in ────────────────────────────
#
# Google sign-in finds an account by ``sub`` first and, failing that, by an
# email its owner has verified — but only on Google's word when Google is
# authoritative for the address. The stub's ``:unverified`` suffix stands in
# for an address it is not.


async def test_a_vouched_email_is_stored_verified_and_normalised(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/google", json={"id_token": "abc123:Ali@Example.com:Ali"}
    )
    user = response.json()["user"]
    assert (user["email"], user["is_email_verified"]) == ("ali@example.com", True)
    assert user["is_phone_verified"] is False


async def test_an_email_google_cannot_vouch_for_is_kept_but_trusted_for_nothing(
    client: AsyncClient, make_user: UserFactory
) -> None:
    owner = await make_user(email="ali@example.com", is_email_verified=True)

    # A different Google account *claiming* the owner's address. Finding the
    # owner by it would hand over the account on the strength of a string.
    response = await client.post(
        "/api/v1/auth/google", json={"id_token": "stranger:ali@example.com:Mallory:unverified"}
    )

    body = response.json()
    assert body["is_new_user"] is True
    assert body["user"]["id"] != str(owner.id)
    assert (body["user"]["email"], body["user"]["is_email_verified"]) == (
        "ali@example.com",
        False,
    )


async def test_a_verified_email_opens_its_account_and_binds_the_google_identity(
    client: AsyncClient, make_user: UserFactory
) -> None:
    owner = await make_user(email="ali@example.com", is_email_verified=True)

    first = await client.post("/api/v1/auth/google", json={"id_token": "g1:ali@example.com:Ali"})
    assert first.json()["is_new_user"] is False
    assert first.json()["user"]["id"] == str(owner.id)
    # The phone it was created with still works; nothing was replaced.
    assert first.json()["user"]["phone"] == owner.phone

    # Bound now, so it is found by ``sub`` even if Google stops vouching.
    later = await client.post(
        "/api/v1/auth/google", json={"id_token": "g1:ali@example.com:Ali:unverified"}
    )
    assert later.json()["user"]["id"] == str(owner.id)


async def test_an_older_account_is_marked_verified_the_next_time_google_vouches(
    client: AsyncClient, make_user: UserFactory
) -> None:
    legacy = await make_user(
        phone=None, auth_method="google", google_sub="google-g1", email="ali@corp.example"
    )

    response = await client.post(
        "/api/v1/auth/google", json={"id_token": "g1:ali@corp.example:Ali"}
    )

    assert response.json()["user"]["id"] == str(legacy.id)
    assert response.json()["user"]["is_email_verified"] is True


async def test_it_is_not_marked_verified_over_an_account_that_already_proved_it(
    client: AsyncClient, make_user: UserFactory
) -> None:
    await make_user(email="ali@corp.example", is_email_verified=True)
    legacy = await make_user(
        phone=None, auth_method="google", google_sub="google-g1", email="ali@corp.example"
    )

    response = await client.post(
        "/api/v1/auth/google", json={"id_token": "g1:ali@corp.example:Ali"}
    )

    # Found by ``sub``, signed in, and left unverified: two accounts cannot
    # both hold one proven address, and the one that proved it first keeps it.
    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == str(legacy.id)
    assert response.json()["user"]["is_email_verified"] is False
