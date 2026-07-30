"""AI lookup endpoint (through the deterministic stub provider)."""

from __future__ import annotations

from httpx import AsyncClient


async def test_lookup_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/api/v1/ai/lookup", json={"term": "run"})
    assert response.status_code == 401


async def test_lookup_returns_multiple_senses(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["term"] == "run"
    assert body["status"] == "ok"
    assert body["notice"] is None
    assert len(body["suggestions"]) == 3
    first = body["suggestions"][0]
    # The card back is exactly these five fields — anything else is a contract
    # change for the "AI Card Magic" screen (docs/ai-card-magic-contract.md).
    assert set(first) == {
        "native_meaning",
        "definition",
        "example",
        "context",
        "part_of_speech",
    }
    assert first["context"] == "Movement"


async def test_lookup_card_back_carries_the_full_design_contract(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Every field the "AI Card Magic" deck renders must be populated."""
    response = await client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})
    first = response.json()["suggestions"][0]
    assert first["part_of_speech"] == "verb"
    assert first["native_meaning"]
    assert first["definition"].startswith("to move using your legs")
    assert first["example"]


async def test_definitions_follow_the_house_dictionary_style(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Longman/Merriam-Webster register: lowercase opening, no trailing full stop.

    Only the stub's fixtures are checkable here — a real provider's prose cannot
    be asserted — but keeping the fixtures honest is what stops the design and the
    prompt drifting apart.
    """
    for term in ("run", "light", "book"):
        response = await client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": term})
        for sense in response.json()["suggestions"]:
            definition = sense["definition"]
            assert definition
            assert not definition.endswith(".")
            first = definition.lstrip("(")[0]
            assert first == first.lower(), definition


async def test_every_sense_of_a_word_gets_its_own_label(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """With no gloss on the card, `context` is what tells sibling senses apart."""
    response = await client.post("/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"})
    contexts = [s["context"] for s in response.json()["suggestions"]]
    assert all(contexts)
    assert len(set(contexts)) == len(contexts)


async def test_lookup_rejects_pasted_prose(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "word " * 100}
    )
    assert response.status_code == 422


async def test_lookup_unknown_word_falls_back_to_generic_sense(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "serendipity"}
    )
    assert response.status_code == 200
    assert len(response.json()["suggestions"]) == 1


async def test_lookup_fallback_is_themed_to_user_interests(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    patched = await client.patch(
        "/api/v1/users/me", headers=auth_headers, json={"interests": ["travel"]}
    )
    assert patched.status_code == 200

    response = await client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "serendipity"}
    )
    assert response.status_code == 200
    suggestion = response.json()["suggestions"][0]
    assert suggestion["context"] == "A Trip Abroad"


async def test_story_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/api/v1/ai/story")
    assert response.status_code == 401


async def test_story_requires_enough_learned_words(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post("/api/v1/ai/story", headers=auth_headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_story_uses_learned_words(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    deck = await client.post(
        "/api/v1/decks", headers=auth_headers, json={"name": "Everyday English", "hue": 262}
    )
    terms = ["reliable", "improve", "borrow"]
    for term in terms:
        created = await client.post(
            "/api/v1/words",
            headers=auth_headers,
            json={"deck_id": deck.json()["id"], "term": term, "meaning": f"meaning of {term}"},
        )
        # Two "easy" grades take a new word from box 1 to box 5 (mastered).
        for _ in range(2):
            graded = await client.post(
                f"/api/v1/study/words/{created.json()['id']}/grade",
                headers=auth_headers,
                json={"grade": "easy"},
            )
            assert graded.status_code == 200
        assert graded.json()["box"] == 5

    response = await client.post("/api/v1/ai/story", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(body["words_used"]) == sorted(terms)
    assert all(term in body["text"] for term in terms)
