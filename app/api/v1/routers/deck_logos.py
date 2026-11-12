"""The logo catalogue: what to draw for each official deck's ``icon`` slug."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.infrastructure.templates.deck_logo_catalog import deck_logo_catalog

router = APIRouter(prefix="/deck-logos", tags=["decks"])

#: Long enough that a list of decks never waits on it, short enough that a new
#: deck's logo reaches a device the day it is published. The ETag makes the
#: re-check after that a 304 with no body.
CACHE_CONTROL = "public, max-age=3600"


class DeckLogoOut(BaseModel):
    #: A number or acronym to set as the badge's title. Empty for a glyph.
    label: str = ""
    #: One-colour SVG markup the client tints and draws. Empty for a label.
    svg: str = ""


class DeckLogoCatalogOut(BaseModel):
    version: str
    logos: dict[str, DeckLogoOut]


@router.get("", response_model=DeckLogoCatalogOut)
async def get_deck_logos(request: Request, response: Response) -> DeckLogoCatalogOut | Response:
    """Every logo, in one response.

    Deliberately unauthenticated: it is the same for everyone, holds nothing
    about anyone, and a client fetches it at launch — before there is a session.
    """
    catalog = deck_logo_catalog()
    etag = f'"{catalog.version}"'
    headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return DeckLogoCatalogOut(
        version=catalog.version,
        logos={
            slug: DeckLogoOut(label=logo.label, svg=logo.svg)
            for slug, logo in catalog.logos.items()
        },
    )
