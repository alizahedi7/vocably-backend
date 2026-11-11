"""A pre-built deck in a language other than English.

The rule these tests pin: a Spanish or German deck is planned, built, copied and
served as that language — and nothing about an English deck changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.deck_build import DeckBuildJobModel
from app.infrastructure.db.repositories.deck_discovery_repository import (
    SqlAlchemyDeckDiscoveryRepository,
)
from app.infrastructure.templates.deck_template_loader import load_template

from .conftest import UserFactory
from .test_deck_build import ScriptedProvider, _service_with

SPANISH_DECK = """\
slug: basics
name: Spanish Basics
language: es
generation:
  native_language: Persian
"""

SPANISH_WORDS = """\
units:
  - name: Unit 1
    words:
      - once
      - pan
"""


@pytest.fixture
def spanish_root(tmp_path: Path) -> Path:
    base = tmp_path / "basics"
    base.mkdir()
    (base / "deck.yaml").write_text(SPANISH_DECK)
    (base / "words.yaml").write_text(SPANISH_WORDS)
    return tmp_path


async def _plan_spanish(
    session_factory: async_sessionmaker[AsyncSession],
    root: Path,
    provider: ScriptedProvider,
    owner_id: object,
) -> DeckBuildJobModel:
    async with session_factory() as session:
        job = await _service_with(session, provider, None).plan(
            load_template("basics", root=root), owner_id=owner_id  # type: ignore[arg-type]
        )
        await session.commit()
    async with session_factory() as session:
        model = await session.get(DeckBuildJobModel, job.id)
        assert model is not None
        return model


async def test_planning_writes_the_language_to_the_deck_and_the_job(
    session_factory: async_sessionmaker[AsyncSession],
    spanish_root: Path,
    make_user: UserFactory,
) -> None:
    owner = await make_user(phone="+989120009101")
    job = await _plan_spanish(session_factory, spanish_root, ScriptedProvider(), owner.id)

    assert job.language == "es"
    async with session_factory() as session:
        deck = await session.get(DeckModel, job.deck_id)
        assert deck is not None
        assert deck.language == "es"


async def test_a_deck_a_learner_built_is_english(
    session_factory: async_sessionmaker[AsyncSession],
    make_user: UserFactory,
) -> None:
    owner = await make_user(phone="+989120009102")
    async with session_factory() as session:
        deck = DeckModel(user_id=owner.id, name="My words", hue=25)
        session.add(deck)
        await session.commit()
        assert deck.language == "en"


async def test_a_copy_from_explore_keeps_the_language(
    session_factory: async_sessionmaker[AsyncSession],
    spanish_root: Path,
    make_user: UserFactory,
) -> None:
    owner = await make_user(phone="+989120009103")
    learner = await make_user(phone="+989120009104")
    job = await _plan_spanish(session_factory, spanish_root, ScriptedProvider(), owner.id)

    async with session_factory() as session:
        assert job.deck_id is not None
        copy = await SqlAlchemyDeckDiscoveryRepository(session).copy_deck_to(
            job.deck_id, learner.id
        )
        await session.commit()
    assert copy.language == "es"

    async with session_factory() as session:
        stored = (await session.execute(select(DeckModel).where(DeckModel.id == copy.id))).scalar_one()
    assert stored.language == "es"
