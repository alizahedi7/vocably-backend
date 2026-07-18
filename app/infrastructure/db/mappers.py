"""Translation between ORM models and pure domain entities.

Keeping this explicit (rather than mapping the ORM classes as entities) is what lets the
domain stay free of SQLAlchemy.
"""

from __future__ import annotations

from app.domain.entities.deck import Deck
from app.domain.entities.otp_challenge import OtpChallenge
from app.domain.entities.user import User
from app.domain.entities.word import Word
from app.domain.enums import AgeRange, AuthMethod, LeitnerBox
from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.otp_challenge import OtpChallengeModel
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.models.word import WordModel


# ── User ─────────────────────────────────────────────────────
def user_to_entity(m: UserModel) -> User:
    return User(
        id=m.id,
        auth_method=AuthMethod(m.auth_method),
        phone=m.phone,
        email=m.email,
        google_sub=m.google_sub,
        name=m.name,
        age_range=AgeRange(m.age_range) if m.age_range else None,
        native_language=m.native_language,
        app_language=m.app_language,
        interests=list(m.interests or []),
        daily_goal=m.daily_goal,
        streak=m.streak,
        last_studied_on=m.last_studied_on,
        onboarded=m.onboarded,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def apply_user(entity: User, m: UserModel) -> None:
    m.auth_method = entity.auth_method.value
    m.phone = entity.phone
    m.email = entity.email
    m.google_sub = entity.google_sub
    m.name = entity.name
    m.age_range = entity.age_range.value if entity.age_range else None
    m.native_language = entity.native_language
    m.app_language = entity.app_language
    m.interests = list(entity.interests)
    m.daily_goal = entity.daily_goal
    m.streak = entity.streak
    m.last_studied_on = entity.last_studied_on
    m.onboarded = entity.onboarded


# ── Deck ─────────────────────────────────────────────────────
def deck_to_entity(m: DeckModel) -> Deck:
    return Deck(
        id=m.id,
        user_id=m.user_id,
        name=m.name,
        hue=m.hue,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def apply_deck(entity: Deck, m: DeckModel) -> None:
    m.user_id = entity.user_id
    m.name = entity.name
    m.hue = entity.hue


# ── Word ─────────────────────────────────────────────────────
def word_to_entity(m: WordModel) -> Word:
    return Word(
        id=m.id,
        deck_id=m.deck_id,
        user_id=m.user_id,
        term=m.term,
        meaning=m.meaning,
        example=m.example,
        sense_label=m.sense_label,
        box=LeitnerBox(m.box),
        due_at=m.due_at,
        review_count=m.review_count,
        last_reviewed_at=m.last_reviewed_at,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def apply_word(entity: Word, m: WordModel) -> None:
    m.user_id = entity.user_id
    m.deck_id = entity.deck_id
    m.term = entity.term
    m.meaning = entity.meaning
    m.example = entity.example
    m.sense_label = entity.sense_label
    m.box = int(entity.box)
    m.due_at = entity.due_at
    m.review_count = entity.review_count
    m.last_reviewed_at = entity.last_reviewed_at


# ── OTP challenge ────────────────────────────────────────────
def otp_to_entity(m: OtpChallengeModel) -> OtpChallenge:
    return OtpChallenge(
        id=m.id,
        phone=m.phone,
        code_hash=m.code_hash,
        expires_at=m.expires_at,
        attempts=m.attempts,
        consumed=m.consumed,
        created_at=m.created_at,
    )


def apply_otp(entity: OtpChallenge, m: OtpChallengeModel) -> None:
    m.phone = entity.phone
    m.code_hash = entity.code_hash
    m.expires_at = entity.expires_at
    m.attempts = entity.attempts
    m.consumed = entity.consumed
