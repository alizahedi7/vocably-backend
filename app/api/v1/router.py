"""Aggregates all v1 routers under a single APIRouter."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routers import ai, auth, decks, study, users, words

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(decks.router)
api_router.include_router(words.router)
api_router.include_router(study.router)
api_router.include_router(ai.router)
