"""What a learner tells us, in the two shapes they tell it.

Two entities, deliberately not one, because they answer different questions and
have different lifetimes:

* :class:`FeedbackReport` is a sentence somebody wrote on purpose — a bug, an
  idea, a complaint. It is rare, high-effort and read by a person.
* :class:`AIFeedback` is a thumb on one AI-written card back. It is frequent,
  effortless and read by an aggregate. Nobody will ever read one of these rows.

Collapsing them into a polymorphic ``feedback`` table would put a nullable
free-text column on a rating and a nullable sense index on a bug report, and
every query would start by filtering on a discriminator. They share nothing but
the word "feedback".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

#: Longest report body accepted. Generous — someone describing a bug properly
#: writes a paragraph — and a hard bound so one caller cannot write a novel
#: into the table.
MAX_REPORT_CHARS = 2000
#: Shortest. "Broken" is a valid, if unhelpful, report; an empty one is a
#: mis-tap. The client's own Send button stays disabled below this, so the
#: server bound is a backstop rather than the thing the learner meets.
MIN_REPORT_CHARS = 4


class FeedbackKind(StrEnum):
    """What the learner says they are telling us.

    Three, and the third is the catch-all on purpose: a form that turns
    somebody away because their complaint does not fit a box collects nothing.
    It is a *triage label*, not a routing decision — nothing branches on it.
    """

    #: Something does not work.
    BUG = "bug"
    #: Something should exist, or work differently.
    IDEA = "idea"
    #: Anything else, including a complaint about how something behaves.
    OTHER = "other"

    @classmethod
    def parse(cls, raw: str | None) -> FeedbackKind:
        """Read a kind off the wire, falling back to :attr:`OTHER`.

        Fails *open*, unlike ``DeckRole.parse``, and for the opposite reason:
        this grants nothing. A build that sends a kind this deploy has not
        heard of must still have its report stored — losing the sentence over
        a label would be the worst possible trade.
        """
        try:
            return cls(raw) if raw else cls.OTHER
        except ValueError:
            return cls.OTHER


class ClientPlatform(StrEnum):
    """Which build the report came from."""

    ANDROID = "android"
    IOS = "ios"
    WEB = "web"
    #: A client that did not say, or said something unrecognised.
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, raw: str | None) -> ClientPlatform:
        try:
            return cls(raw) if raw else cls.UNKNOWN
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class ClientContext:
    """Where the report was written, as the device could describe itself.

    Everything here is what triage needs and nothing more. There is no device
    id, no advertising id, no IP, and no free-form diagnostic blob: the report
    already carries a user id, which is the only identifier anyone answering it
    needs, and the rest would be data we cannot justify holding.

    Every field is optional and every field is truncated rather than rejected —
    a report must never be lost over its metadata. The client shows the learner
    exactly these four values before they send (see the Flutter
    ``feedback_screen.dart``), which is the other half of collecting them
    honestly.
    """

    #: As the client reports it, e.g. ``"1.5.0+11"``.
    app_version: str = ""
    platform: ClientPlatform = ClientPlatform.UNKNOWN
    #: Whatever the platform says about itself. On Android Dart's
    #: ``Platform.operatingSystemVersion`` already includes the release, the SDK
    #: level and the build fingerprint, which is why no device-info dependency
    #: was added for a model name.
    os_version: str = ""
    #: The app's *interface* language when the report was written ("en"/"fa") —
    #: which is a fact about how the screen looked, and so about the bug.
    locale: str = ""


@dataclass(slots=True)
class FeedbackReport:
    """One thing a learner wrote to us."""

    id: UUID = field(default_factory=uuid4)
    #: ``None`` once the account is deleted. The row survives the account
    #: (``ON DELETE SET NULL``): the bug is not the person, and losing the
    #: report would lose the bug.
    user_id: UUID | None = None
    kind: FeedbackKind = FeedbackKind.OTHER
    message: str = ""
    context: ClientContext = field(default_factory=ClientContext)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class AIRating(StrEnum):
    """A thumb on one AI-written card back.

    :attr:`NONE` is a wire value, never a stored one: it is how the client says
    "I have taken my rating back", which is what tapping the lit thumb again
    does. The service deletes the row rather than storing an opinion nobody
    holds, so a withdrawn rating cannot quietly go on counting.
    """

    UP = "up"
    DOWN = "down"
    NONE = "none"

    @classmethod
    def parse(cls, raw: str | None) -> AIRating:
        try:
            return cls(raw) if raw else cls.NONE
        except ValueError:
            return cls.NONE


class AIFeedbackReason(StrEnum):
    """Why a card back was marked down.

    Three, because they sit as three chips in the footer of a 330px card and a
    fourth would wrap. They are the three ways a card back is wrong that the
    learner can see without leaving the screen: the headline is wrong, the
    sentence is wrong, or the whole sense is the wrong one for the word.

    Always optional, and always *after* the rating has already been recorded —
    see :meth:`FeedbackService.rate_ai_sense`. A reason is a bonus, never a
    condition of being heard.
    """

    #: The native-language headline is wrong or misleading.
    WRONG_MEANING = "wrong_meaning"
    #: The example sentence is wrong, unnatural, or does not use the word.
    BAD_EXAMPLE = "bad_example"
    #: The sense itself is not one this word has, or not the one asked for.
    WRONG_SENSE = "wrong_sense"

    @classmethod
    def parse(cls, raw: str | None) -> AIFeedbackReason | None:
        if not raw:
            return None
        try:
            return cls(raw)
        except ValueError:
            return None


@dataclass(slots=True)
class AIFeedback:
    """One learner's verdict on one sense of one lookup.

    Identified by ``(user_id, lookup_id, sense_index)`` and stored as an upsert,
    so changing your mind moves a row rather than adding one. That is what makes
    "how many people found this card good" a straight count instead of a
    last-per-user window, and it is also what makes the endpoint safe to retry —
    which matters, because the client fires it silently and never tells the
    learner it failed.

    The rated *text* is not copied here. ``lookup_id`` is the ``entry_hash`` of
    :class:`~app.infrastructure.db.models.ai_lookup.AILookupEntryModel`, whose
    ``payload`` already holds every sense the provider returned — re-storing it
    per rating would duplicate a paid-for corpus to say nothing new. What *is*
    copied is the handful of facts that outlive that row: the term, and which
    prompt and model produced it. A retired prompt version gets swept out of the
    cache; the verdict on it is exactly what you want to keep.
    """

    id: UUID = field(default_factory=uuid4)
    #: ``None`` once the account is deleted. A rating is not personal data —
    #: its value is entirely in ``(lookup_id, sense_index, rating)`` — so it is
    #: anonymised rather than destroyed.
    user_id: UUID | None = None
    #: The lookup this sense came from: ``LookupCacheKey.digest()`` for the
    #: resolved term. Deterministic, so the same word looked up by two learners
    #: with the same profile aggregates into one score.
    lookup_id: str = ""
    #: Which card back in the deck, 0-based, as the client dealt them.
    sense_index: int = 0
    rating: AIRating = AIRating.UP
    reason: AIFeedbackReason | None = None
    #: Denormalised from the cache entry so the row still says *which word* once
    #: the entry it pointed at has been swept. Empty when the entry could not be
    #: resolved at all.
    term: str = ""
    native_language: str = ""
    prompt_version: int = 0
    provider: str = ""
    model: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
