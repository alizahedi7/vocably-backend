"""The docs surface is mounted from configuration, not unconditionally.

``Settings.docs_enabled`` is covered in test_config.py; what these assert is the
other half — that ``create_app`` actually honours it, over HTTP, for *all three*
routes. ``/redoc`` is the one worth a test of its own: it has a non-None default
in FastAPI, so it was being published in production by omission rather than by
any decision, and a future refactor that rebuilds this call could reintroduce it
the same silent way.

These drive the app through ``TestClient`` rather than reading ``app.routes``,
which does not flatten included routers — and because 404 vs. a broken empty page
is the distinction that actually matters here.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.unit.test_config import production_settings

DOCS_ROUTES = ("/docs", "/redoc", "/openapi.json")


def _client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("app.main.settings", settings)
    return TestClient(create_app())


def test_production_serves_no_docs_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(production_settings(), monkeypatch)
    for path in DOCS_ROUTES:
        assert client.get(path).status_code == 404, path


def test_development_serves_all_three_docs_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(Settings(environment="development"), monkeypatch)
    for path in DOCS_ROUTES:
        assert client.get(path).status_code == 200, path


def test_expose_docs_restores_them_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """A staging deployment runs ENVIRONMENT=production with docs deliberately on."""
    client = _client(production_settings(expose_docs=True), monkeypatch)
    for path in DOCS_ROUTES:
        assert client.get(path).status_code == 200, path


def test_the_api_itself_is_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turning docs off must not be a way to turn the product off."""
    client = _client(production_settings(), monkeypatch)
    assert client.get("/health").status_code == 200
    # Reached the router and was rejected on its own terms (401), rather than
    # 404 — i.e. the v1 surface is still mounted.
    assert client.get("/api/v1/users/me").status_code == 401
