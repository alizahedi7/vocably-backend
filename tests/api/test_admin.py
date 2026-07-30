"""Admin dashboard endpoints: role gating and read-only aggregations."""

from __future__ import annotations

from httpx import AsyncClient

from tests.api.conftest import UserFactory, bearer


async def _admin_headers(make_user: UserFactory) -> dict[str, str]:
    admin = await make_user(phone="+989120000777", name="Root", is_admin=True)
    return bearer(admin.id)


async def test_admin_routes_require_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/v1/admin/overview")
    assert response.status_code == 401


async def test_admin_routes_reject_non_admins(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # ``auth_headers`` belongs to a regular (non-admin) user.
    response = await client.get("/api/v1/admin/overview", headers=auth_headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


async def test_overview_aggregates_platform_totals(
    client: AsyncClient, make_user: UserFactory
) -> None:
    headers = await _admin_headers(make_user)
    await make_user(phone="+989120000001", onboarded=True)
    await make_user(phone="+989120000002", onboarded=False)

    response = await client.get("/api/v1/admin/overview", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    # 1 admin + 2 extra users created here.
    assert body["totalUsers"] == 3
    assert body["totalDecks"] == 0
    assert body["totalWords"] == 0
    assert 0.0 <= body["onboardedRate"] <= 1.0
    assert "newUsersLast7d" in body
    assert "activeUsersLast7d" in body


async def test_users_listing_includes_per_user_counts(
    client: AsyncClient, make_user: UserFactory
) -> None:
    headers = await _admin_headers(make_user)
    member = await make_user(phone="+989120000123", name="Sara")

    deck = await client.post(
        "/api/v1/decks", headers=bearer(member.id), json={"name": "Travel", "hue": 190}
    )
    deck_id = deck.json()["id"]
    await client.post(
        "/api/v1/words",
        headers=bearer(member.id),
        json={"deck_id": deck_id, "term": "reliable", "meaning": "able to be trusted"},
    )

    response = await client.get("/api/v1/admin/users", headers=headers)
    assert response.status_code == 200
    rows = {row["name"]: row for row in response.json()}
    assert rows["Sara"]["deckCount"] == 1
    assert rows["Sara"]["wordCount"] == 1
    assert rows["Sara"]["authMethod"] == "phone"
    assert "registeredAt" in rows["Sara"]
    assert rows["Sara"]["lastLoginAt"] is None  # created directly, never logged in


async def test_categories_and_words_carry_owner_context(
    client: AsyncClient, make_user: UserFactory
) -> None:
    headers = await _admin_headers(make_user)
    member = await make_user(phone="+989120000123", name="Sara")

    deck = await client.post(
        "/api/v1/decks", headers=bearer(member.id), json={"name": "Travel", "hue": 190}
    )
    deck_id = deck.json()["id"]
    await client.post(
        "/api/v1/words",
        headers=bearer(member.id),
        json={"deck_id": deck_id, "term": "reliable", "meaning": "able to be trusted"},
    )

    categories = await client.get("/api/v1/admin/categories", headers=headers)
    assert categories.status_code == 200
    (category,) = categories.json()
    assert category["name"] == "Travel"
    assert category["ownerName"] == "Sara"
    assert category["wordCount"] == 1

    words = await client.get("/api/v1/admin/words", headers=headers)
    assert words.status_code == 200
    (word,) = words.json()
    assert word["term"] == "reliable"
    assert word["categoryName"] == "Travel"
    assert word["ownerName"] == "Sara"


async def test_registrations_series_is_contiguous(
    client: AsyncClient, make_user: UserFactory
) -> None:
    headers = await _admin_headers(make_user)

    response = await client.get("/api/v1/admin/registrations?days=7", headers=headers)
    assert response.status_code == 200
    series = response.json()
    assert len(series) == 7  # zero-filled, one point per day
    assert sum(point["value"] for point in series) >= 1  # the admin registered today


async def test_auth_methods_breakdown(client: AsyncClient, make_user: UserFactory) -> None:
    headers = await _admin_headers(make_user)
    await make_user(phone=None, google_sub="g-1", email="a@example.com", auth_method="google")

    response = await client.get("/api/v1/admin/auth-methods", headers=headers)
    assert response.status_code == 200
    counts = {row["method"]: row["count"] for row in response.json()}
    assert counts.get("phone", 0) >= 1
    assert counts.get("google", 0) == 1


async def test_cache_routes_reject_non_admins(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    for path in ("/admin/cache/overview", "/admin/cache/entries"):
        response = await client.get(f"/api/v1{path}", headers=auth_headers)
        assert response.status_code == 403


async def test_cache_overview_reports_prompt_version_and_empty_totals(
    client: AsyncClient, make_user: UserFactory
) -> None:
    from app.infrastructure.ai.prompts import PROMPT_VERSION

    headers = await _admin_headers(make_user)

    response = await client.get("/api/v1/admin/cache/overview", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totalEntries"] == 0
    assert body["totalAliases"] == 0
    assert body["totalHits"] == 0
    assert body["currentPromptVersion"] == PROMPT_VERSION
    assert body["staleEntryCount"] == 0
    assert body["expiredAliasCount"] == 0


async def test_cache_entries_lists_hit_counts_and_paginates(
    client: AsyncClient, make_user: UserFactory, auth_headers: dict[str, str]
) -> None:
    headers = await _admin_headers(make_user)

    for _ in range(2):
        await client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})

    response = await client.get("/api/v1/admin/cache/entries?limit=1&offset=0", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    (entry,) = body["items"]
    assert entry["term"] == "run"
    assert entry["hitCount"] == 1  # one miss (write) + one hit (read)
    assert entry["aliasCount"] == 1
    assert entry["lastAccessedAt"] is not None


async def test_cache_entry_by_id_returns_404_when_missing(
    client: AsyncClient, make_user: UserFactory
) -> None:
    headers = await _admin_headers(make_user)
    missing_id = "00000000-0000-0000-0000-000000000000"

    response = await client.get(f"/api/v1/admin/cache/entries/{missing_id}", headers=headers)
    assert response.status_code == 404


async def test_cache_entry_by_id_returns_the_entry(
    client: AsyncClient, make_user: UserFactory, auth_headers: dict[str, str]
) -> None:
    headers = await _admin_headers(make_user)
    await client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})

    entries = await client.get("/api/v1/admin/cache/entries", headers=headers)
    (entry,) = entries.json()["items"]

    response = await client.get(f"/api/v1/admin/cache/entries/{entry['id']}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["term"] == "run"


async def test_cache_entry_aliases_lists_the_learner_inputs_that_resolved_to_it(
    client: AsyncClient, make_user: UserFactory, auth_headers: dict[str, str]
) -> None:
    headers = await _admin_headers(make_user)
    await client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})

    entries = await client.get("/api/v1/admin/cache/entries", headers=headers)
    (entry,) = entries.json()["items"]

    response = await client.get(
        f"/api/v1/admin/cache/entries/{entry['id']}/aliases", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    (alias,) = body["items"]
    assert alias["normalizedInput"] == "run"
    assert alias["resolvedTerm"] == "run"
