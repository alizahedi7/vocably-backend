"""Aggregates all v1 routers under a single APIRouter."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routers import (
    admin,
    ai,
    auth,
    deck_sharing,
    deck_units,
    decks,
    discovery,
    feedback,
    study,
    users,
    words,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(discovery.friends_router)
# Literal /decks/* paths must be registered before decks.router, whose
# /decks/{deck_id} would otherwise match "public", "shared" and "join" and
# answer 422 for a malformed UUID.
api_router.include_router(discovery.decks_router)
api_router.include_router(deck_sharing.router)
api_router.include_router(decks.router)
api_router.include_router(deck_units.deck_units_router)
api_router.include_router(deck_units.units_router)
api_router.include_router(words.router)
api_router.include_router(study.router)
api_router.include_router(ai.router)
api_router.include_router(feedback.router)
api_router.include_router(admin.router)
