"""Translation between ORM models and pure domain entities.

Keeping this explicit (rather than mapping the ORM classes as entities) is what lets the
domain stay free of SQLAlchemy.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.entities.deck import Deck
from app.domain.entities.deck_build import DeckBuildItem, DeckBuildJob, SenseHint
from app.domain.entities.deck_member import DeckMember
from app.domain.entities.deck_unit import DeckUnit
from app.domain.entities.lexeme import Lexeme, LexemeSense, SenseTranslation
from app.domain.entities.otp_challenge import OtpChallenge
from app.domain.entities.review_event import ReviewEvent
from app.domain.entities.user import User
from app.domain.entities.word import Word
from app.domain.entities.word_progress import WordProgress
from app.domain.enums import (
    AgeRange,
    AuthMethod,
    DeckBuildItemState,
    DeckBuildState,
    DeckRole,
    LeitnerBox,
    ReviewGrade,
    SenseSelection,
    SenseSource,
    SenseStatus,
)
from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.deck_build import DeckBuildItemModel, DeckBuildJobModel
from app.infrastructure.db.models.deck_member import DeckMemberModel
from app.infrastructure.db.models.deck_unit import DeckUnitModel
from app.infrastructure.db.models.lexicon import (
    LexemeModel,
    LexemeSenseModel,
    LexemeSenseTranslationModel,
)
from app.infrastructure.db.models.otp_challenge import OtpChallengeModel
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.models.word import WordModel
from app.infrastructure.db.models.word_progress import WordProgressModel
from app.infrastructure.db.models.word_review import WordReviewModel


# ── User ─────────────────────────────────────────────────────
def user_to_entity(m: UserModel) -> User:
    return User(
        id=m.id,
        auth_method=AuthMethod(m.auth_method),
        phone=m.phone,
        email=m.email,
        google_sub=m.google_sub,
        name=m.name,
        username=m.username,
        age_range=AgeRange(m.age_range) if m.age_range else None,
        native_language=m.native_language,
        app_language=m.app_language,
        target_language=m.target_language,
        proficiency=m.proficiency,
        study_time=m.study_time,
        timezone=m.timezone,
        interests=list(m.interests or []),
        daily_goal=m.daily_goal,
        streak=m.streak,
        xp=m.xp,
        last_studied_on=m.last_studied_on,
        streak_last_day=m.streak_last_day,
        streak_banked_on=m.streak_banked_on,
        onboarded=m.onboarded,
        is_admin=m.is_admin,
        last_login_at=m.last_login_at,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def apply_user(entity: User, m: UserModel) -> None:
    m.auth_method = entity.auth_method.value
    m.phone = entity.phone
    m.email = entity.email
    m.google_sub = entity.google_sub
    m.name = entity.name
    m.username = entity.username
    m.age_range = entity.age_range.value if entity.age_range else None
    m.native_language = entity.native_language
    m.app_language = entity.app_language
    m.target_language = entity.target_language
    m.proficiency = entity.proficiency
    m.study_time = entity.study_time
    m.timezone = entity.timezone
    m.interests = list(entity.interests)
    m.daily_goal = entity.daily_goal
    # xp, the three streak columns and goal_celebrated_on are deliberately
    # absent: each is maintained in SQL — XpRepository.award,
    # UserRepository.bank_day, settle_streak and claim_goal_celebration — and
    # writing a read-then-written value here would clobber a concurrent award,
    # a day banked on the learner's other device, or a celebration that device
    # has already been given.
    m.last_studied_on = entity.last_studied_on
    m.onboarded = entity.onboarded
    m.is_admin = entity.is_admin
    m.last_login_at = entity.last_login_at


# ── Deck ─────────────────────────────────────────────────────
def deck_to_entity(m: DeckModel) -> Deck:
    return Deck(
        id=m.id,
        user_id=m.user_id,
        name=m.name,
        hue=m.hue,
        icon=m.icon,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def apply_deck(entity: Deck, m: DeckModel) -> None:
    m.user_id = entity.user_id
    m.name = entity.name
    m.hue = entity.hue
    m.icon = entity.icon


# ── Word ─────────────────────────────────────────────────────
def word_to_entity(m: WordModel) -> Word:
    return Word(
        id=m.id,
        deck_id=m.deck_id,
        created_by_user_id=m.created_by_user_id,
        unit_id=m.unit_id,
        term=m.term,
        meaning=m.meaning,
        definition=m.definition,
        example=m.example,
        sense_label=m.sense_label,
        phonetic=m.phonetic,
        lexeme_sense_id=m.lexeme_sense_id,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def apply_word(entity: Word, m: WordModel) -> None:
    # created_by_user_id is attribution and is set once, at insert. Leaving it
    # out of the update path means an edit by a co-editor cannot silently
    # reassign authorship of someone else's card.
    if entity.created_by_user_id is not None:
        m.created_by_user_id = entity.created_by_user_id
    m.deck_id = entity.deck_id
    m.unit_id = entity.unit_id
    m.term = entity.term
    m.meaning = entity.meaning
    m.definition = entity.definition
    m.example = entity.example
    m.sense_label = entity.sense_label
    m.phonetic = entity.phonetic
    # Provenance, like created_by_user_id: set when the pipeline builds the card
    # and never cleared by a later edit, so a learner rewording their copy does
    # not erase the record of which shared sense it came from.
    if entity.lexeme_sense_id is not None:
        m.lexeme_sense_id = entity.lexeme_sense_id


# ── Word progress ────────────────────────────────────────────
def word_progress_to_entity(m: WordProgressModel) -> WordProgress:
    return WordProgress(
        user_id=m.user_id,
        word_id=m.word_id,
        deck_id=m.deck_id,
        box=LeitnerBox(m.box),
        due_at=m.due_at,
        review_count=m.review_count,
        last_reviewed_at=m.last_reviewed_at,
        lapse_count=m.lapse_count,
        consecutive_correct=m.consecutive_correct,
        first_reviewed_at=m.first_reviewed_at,
        mastered_at=m.mastered_at,
        last_grade=ReviewGrade.from_ordinal(m.last_grade) if m.last_grade is not None else None,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def word_progress_values(entity: WordProgress) -> dict[str, object]:
    """Column values for the upsert a grade issues.

    A dict rather than an ``apply_*`` mutator because progress rows are written
    with ``INSERT … ON CONFLICT DO UPDATE``: the row may not exist yet, and
    read-then-write would lose one of two concurrent first grades of the same
    word. ``created_at`` is omitted so the database defaults it on insert and
    leaves it alone on update — it records when the learner first met the word.
    """
    return {
        "user_id": entity.user_id,
        "word_id": entity.word_id,
        "deck_id": entity.deck_id,
        "box": int(entity.box),
        "due_at": entity.due_at,
        "review_count": entity.review_count,
        "last_reviewed_at": entity.last_reviewed_at,
        "lapse_count": entity.lapse_count,
        "consecutive_correct": entity.consecutive_correct,
        "first_reviewed_at": entity.first_reviewed_at,
        "mastered_at": entity.mastered_at,
        "last_grade": entity.last_grade.ordinal if entity.last_grade is not None else None,
        "updated_at": entity.updated_at,
    }


# ── Deck membership ──────────────────────────────────────────
def deck_member_to_entity(m: DeckMemberModel) -> DeckMember:
    return DeckMember(
        deck_id=m.deck_id,
        user_id=m.user_id,
        role=DeckRole.parse(m.role),
        invited_by_user_id=m.invited_by_user_id,
        joined_at=m.joined_at,
        self_paced=m.self_paced,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


# ── Deck unit ────────────────────────────────────────────────
def deck_unit_to_entity(m: DeckUnitModel) -> DeckUnit:
    return DeckUnit(
        id=m.id,
        deck_id=m.deck_id,
        name=m.name,
        position=m.position,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


# ── Review event ─────────────────────────────────────────────
def review_event_to_entity(m: WordReviewModel) -> ReviewEvent:
    return ReviewEvent(
        id=m.id,
        user_id=m.user_id,
        word_id=m.word_id,
        deck_id=m.deck_id,
        reviewed_at=m.reviewed_at,
        grade=ReviewGrade.from_ordinal(m.grade),
        box_before=LeitnerBox(m.box_before),
        box_after=LeitnerBox(m.box_after),
        elapsed_seconds=m.elapsed_seconds,
        overdue_seconds=m.overdue_seconds,
        latency_ms=m.latency_ms,
        session_id=m.session_id,
    )


def review_event_values(entity: ReviewEvent) -> dict[str, object]:
    """Column values for a Core INSERT.

    Events are append-only, so there is no ``apply_*`` counterpart: nothing ever
    writes over an existing row. ``id`` is omitted so the database assigns it.
    """
    return {
        "user_id": entity.user_id,
        "word_id": entity.word_id,
        "deck_id": entity.deck_id,
        "reviewed_at": entity.reviewed_at,
        "grade": entity.grade.ordinal,
        "box_before": int(entity.box_before),
        "box_after": int(entity.box_after),
        "elapsed_seconds": entity.elapsed_seconds,
        "overdue_seconds": entity.overdue_seconds,
        "latency_ms": entity.latency_ms,
        "session_id": entity.session_id,
    }


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


# ── Lexicon ──────────────────────────────────────────────────
def lexeme_sense_translation_to_entity(m: LexemeSenseTranslationModel) -> SenseTranslation:
    return SenseTranslation(
        id=m.id,
        sense_id=m.sense_id,
        native_language=m.native_language,
        native_meaning=m.native_meaning,
        status=SenseStatus(m.status),
        content_version=m.content_version,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def lexeme_sense_to_entity(
    m: LexemeSenseModel,
    translations: list[LexemeSenseTranslationModel] | None = None,
) -> LexemeSense:
    return LexemeSense(
        id=m.id,
        lexeme_id=m.lexeme_id,
        sense_key=m.sense_key,
        register=m.register,
        position=m.position,
        part_of_speech=m.part_of_speech,
        context=m.context,
        definition=m.definition,
        example=m.example,
        status=SenseStatus(m.status),
        content_version=m.content_version,
        provider=m.provider,
        model=m.model,
        source=SenseSource(m.source),
        translations=[lexeme_sense_translation_to_entity(t) for t in (translations or [])],
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def lexeme_to_entity(m: LexemeModel, senses: list[LexemeSense] | None = None) -> Lexeme:
    return Lexeme(
        id=m.id,
        lemma=m.lemma,
        language=m.language,
        display_term=m.display_term,
        phonetic=m.phonetic,
        senses=senses or [],
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


# ── Deck builds ──────────────────────────────────────────────
def deck_build_job_to_entity(m: DeckBuildJobModel) -> DeckBuildJob:
    return DeckBuildJob(
        id=m.id,
        template_slug=m.template_slug,
        template_version=m.template_version,
        template_hash=m.template_hash,
        deck_id=m.deck_id,
        state=DeckBuildState(m.state),
        content_version=m.content_version,
        native_language=m.native_language,
        register=m.register,
        category=m.category,
        strategies=tuple(SenseSelection(name) for name in m.strategies.split(",") if name),
        items_total=m.items_total,
        items_done=m.items_done,
        items_failed=m.items_failed,
        lexemes_reused=m.lexemes_reused,
        lexemes_generated=m.lexemes_generated,
        senses_enriched=m.senses_enriched,
        ai_calls=m.ai_calls,
        created_by_user_id=m.created_by_user_id,
        started_at=m.started_at,
        finished_at=m.finished_at,
        last_error=m.last_error,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def deck_build_item_to_entity(m: DeckBuildItemModel) -> DeckBuildItem:
    raw = m.hint or {}
    return DeckBuildItem(
        id=m.id,
        job_id=m.job_id,
        position=m.position,
        unit_label=m.unit_label,
        unit_position=m.unit_position,
        source_term=m.source_term,
        normalized=m.normalized,
        hint=SenseHint(
            part_of_speech=str(raw.get("part_of_speech") or ""),
            context=str(raw.get("context") or ""),
            gloss=str(raw.get("gloss") or ""),
        ),
        state=DeckBuildItemState(m.state),
        lexeme_id=m.lexeme_id,
        sense_id=m.sense_id,
        word_id=m.word_id,
        selection=SenseSelection(m.selection) if m.selection else None,
        selection_score=m.selection_score,
        attempts=m.attempts,
        next_attempt_at=m.next_attempt_at,
        last_error=m.last_error,
        enriched=m.enriched,
        claimed_at=m.claimed_at,
        updated_at=m.updated_at or datetime.now(UTC),
    )


def hint_to_payload(hint: SenseHint) -> dict[str, str] | None:
    """Store nothing rather than three empty strings for the common case."""
    if hint.is_empty:
        return None
    return {
        "part_of_speech": hint.part_of_speech,
        "context": hint.context,
        "gloss": hint.gloss,
    }
