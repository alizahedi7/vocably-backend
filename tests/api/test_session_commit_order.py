"""The request's transaction commits before its response is sent.

At FastAPI's default dependency scope the session committed *after* the response
went out: a client could be told 201 for a row the next request could not read
yet (a 404, seen under load), or for a row whose commit then failed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app


async def test_the_commit_lands_before_the_response_starts(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    real_commit = AsyncSession.commit

    async def recording_commit(self: AsyncSession) -> None:
        order.append("commit")
        await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", recording_commit)

    body = json.dumps({"name": "ordered"}).encode()
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            order.append(f"response {message['status']}")

    headers = [(b"content-type", b"application/json")] + [
        (k.lower().encode(), v.encode()) for k, v in auth_headers.items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/decks",
        "raw_path": b"/api/v1/decks",
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    await app(scope, receive, send)

    assert order == ["commit", "response 201"]
