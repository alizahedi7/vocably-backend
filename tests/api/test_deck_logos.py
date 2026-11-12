"""The logo catalogue: one public document, cacheable, and safe to render."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.infrastructure.templates.deck_logo_catalog import (
    DeckLogoError,
    deck_logo_catalog,
    load_catalog,
)
from app.infrastructure.templates.deck_template_loader import list_templates, load_template

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24v24z"/></svg>'


def catalog_dir(tmp_path: Path, manifest: str, **glyphs: str) -> Path:
    (tmp_path / "logos.yaml").write_text(manifest)
    for name, body in glyphs.items():
        (tmp_path / f"{name}.svg").write_text(body)
    return tmp_path


async def test_the_catalogue_needs_no_session_and_carries_both_kinds(client: AsyncClient) -> None:
    response = await client.get("/api/v1/deck-logos")

    assert response.status_code == 200
    body = response.json()
    assert body["logos"]["504"] == {"label": "504", "svg": ""}
    assert body["logos"]["travel"]["svg"].startswith("<svg")
    assert response.headers["cache-control"] == "public, max-age=3600"


async def test_a_client_holding_this_version_is_answered_with_no_body(client: AsyncClient) -> None:
    etag = (await client.get("/api/v1/deck-logos")).headers["etag"]

    again = await client.get("/api/v1/deck-logos", headers={"If-None-Match": etag})

    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == etag


def test_every_shipped_template_names_a_logo_the_catalogue_holds() -> None:
    """A deck whose slug is missing draws an initial, silently, in production."""
    catalog = deck_logo_catalog()
    for slug in list_templates():
        icon = load_template(slug).icon
        assert not icon or catalog.has(icon), f"{slug} names icon {icon!r}"


def test_the_version_moves_with_the_content(tmp_path: Path) -> None:
    before = load_catalog(catalog_dir(tmp_path, 'a: {label: "A"}')).version
    after = load_catalog(catalog_dir(tmp_path, 'a: {label: "B"}')).version
    assert before != after


@pytest.mark.parametrize(
    "glyph",
    [
        SVG.replace("<path", "<script>alert(1)</script><path"),
        SVG.replace("<path", '<path onclick="x()"'),
        SVG.replace("<path", '<image href="https://example.com/x.png"/><path'),
        SVG.replace('viewBox="0 0 24 24"', 'viewBox="0 0 24 12"'),
        SVG + "<p>trailing</p>",
        SVG.replace("</svg>", "<!-- " + "x" * 5000 + " --></svg>"),
    ],
)
def test_a_glyph_that_could_run_or_load_anything_is_refused(tmp_path: Path, glyph: str) -> None:
    with pytest.raises(DeckLogoError):
        load_catalog(catalog_dir(tmp_path, "x: {glyph: x.svg}", x=glyph))


@pytest.mark.parametrize(
    "manifest",
    [
        'x: {label: "TOOLONG7"}',
        'x: {label: "A", glyph: x.svg}',
        "x: {glyph: ../x.svg}",
        "x: {glyph: missing.svg}",
        'Bad_Slug: {label: "A"}',
    ],
)
def test_a_malformed_entry_is_refused(tmp_path: Path, manifest: str) -> None:
    with pytest.raises(DeckLogoError):
        load_catalog(catalog_dir(tmp_path, manifest, x=SVG))
