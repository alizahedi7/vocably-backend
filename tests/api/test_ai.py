"""AI lookup endpoint (through the deterministic stub provider)."""

from __future__ import annotations

from httpx import AsyncClient


async def test_lookup_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/api/v1/ai/lookup", json={"term": "run"})
    assert response.status_code == 401


async def test_lookup_returns_multiple_senses(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "run"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["term"] == "run"
    assert len(body["suggestions"]) == 3
    first = body["suggestions"][0]
    assert set(first) == {"meaning", "context", "example"}
    assert first["context"] == "movement"


async def test_lookup_unknown_word_falls_back_to_generic_sense(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/ai/lookup", headers=auth_headers, json={"term": "serendipity"}
    )
    assert response.status_code == 200
    assert len(response.json()["suggestions"]) == 1
