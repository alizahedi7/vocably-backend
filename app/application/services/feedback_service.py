"""Feedback use cases: a written report, and a thumb on an AI card back.

The two halves are held to opposite standards, and that is the whole design.

A **report** is loud. It is validated properly, it fails with something the
learner can read and act on, and the client shows them it was received. Somebody
took the trouble to write it, so losing it silently would be the worst outcome
available.

A **rating** is silent. It is fire-and-forget from a widget the learner may not
even remember tapping, and it must be impossible for it to produce a spinner, a
dialog, an error or a retry prompt. So the rules run the other way: anything that
can be tolerated is tolerated, everything is idempotent, and the only rejections
left are the two that would corrupt the data rather than inconvenience anybody.
"""

from __future__ import annotations

from uuid import UUID

from app.application.ports.feedback_notifier import FeedbackNotifier
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.domain.entities.feedback import (
    MAX_REPORT_CHARS,
    MIN_REPORT_CHARS,
    AIFeedback,
    AIFeedbackReason,
    AIRating,
    ClientContext,
    ClientPlatform,
    FeedbackKind,
    FeedbackReport,
)
from app.domain.repositories.feedback_repository import (
    AIFeedbackTotals,
    AISenseScore,
    FeedbackRepository,
)

logger = get_logger("vocably.feedback")

#: Column widths from ``FeedbackReportModel``. Metadata is *truncated* to these
#: rather than validated against them: a report must never be refused over the
#: shape of a string the learner did not write.
_MAX_APP_VERSION = 32
_MAX_OS_VERSION = 120
_MAX_LOCALE = 16

#: How many reports one admin page holds, and the cap on what may be asked for.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class FeedbackService:
    def __init__(
        self,
        feedback: FeedbackRepository,
        notifier: FeedbackNotifier,
        *,
        prompt_version: int,
        provider: str,
        model: str,
    ) -> None:
        self._feedback = feedback
        self._notifier = notifier
        # The AI configuration this deployment is running, injected the way
        # ``LexiconService`` takes its ``content_version``: the application layer
        # must not reach into ``infrastructure.ai.factory`` to ask. Used only as
        # the fallback when the cache cannot say what produced a deck.
        self._prompt_version = prompt_version
        self._provider = provider
        self._model = model

    # ── a written report ─────────────────────────────────────
    async def submit_report(
        self,
        user_id: UUID,
        *,
        kind: str | None,
        message: str,
        app_version: str = "",
        platform: str | None = None,
        os_version: str = "",
        locale: str = "",
    ) -> FeedbackReport:
        """Store one report, then tell whoever is listening.

        The only thing that can be rejected here is the message itself, because
        it is the only thing the learner actually typed and so the only thing
        they can fix. An unrecognised ``kind`` becomes ``other``, and every piece
        of metadata is truncated to fit: none of it is worth a 422 that costs
        somebody the paragraph they just wrote.
        """
        body = message.strip()
        if len(body) < MIN_REPORT_CHARS:
            raise ValidationError("Tell us a little more about what happened.")
        if len(body) > MAX_REPORT_CHARS:
            raise ValidationError(f"Keep it under {MAX_REPORT_CHARS} characters.")

        report = FeedbackReport(
            user_id=user_id,
            kind=FeedbackKind.parse(kind),
            message=body,
            context=ClientContext(
                app_version=app_version.strip()[:_MAX_APP_VERSION],
                platform=ClientPlatform.parse(platform),
                os_version=os_version.strip()[:_MAX_OS_VERSION],
                locale=locale.strip()[:_MAX_LOCALE],
            ),
        )
        stored = await self._feedback.add_report(report)
        await self._announce(stored)
        return stored

    async def _announce(self, report: FeedbackReport) -> None:
        """Notify, and never let the notification break the submit.

        The report is already written by the time this runs. A channel that is
        misconfigured or down is ours to fix, and turning it into a 502 would
        throw away the report *and* tell the learner their message failed when it
        did not.
        """
        try:
            await self._notifier.notify_report(report)
        except Exception:  # noqa: BLE001 — a stored report must survive a bad channel
            logger.warning("failed to announce feedback report %s", report.id, exc_info=True)

    # ── a thumb on an AI card back ───────────────────────────
    async def rate_ai_sense(
        self,
        user_id: UUID,
        *,
        lookup_id: str,
        sense_index: int,
        rating: str | None,
        reason: str | None = None,
    ) -> AIFeedback | None:
        """Record, move, or withdraw one learner's verdict on one card back.

        Returns ``None`` when the verdict was withdrawn — the client's own state
        is already showing that, so there is nothing to send back.

        Two rejections survive, and only two:

        * a missing ``lookup_id``, which is not a rating of anything;
        * a ``sense_index`` the deck provably did not have, which we can only
          say when the cache still holds the entry.

        Everything else bends. An unreadable ``rating`` reads as a withdrawal
        rather than a 422, an unreadable ``reason`` is dropped and the rating is
        still stored, and an entry we cannot resolve is accepted with blank
        provenance — because from the learner's side all of these are a thumb
        that lit up, and there is no place on the screen to explain otherwise.
        """
        key = lookup_id.strip()
        if not key:
            raise ValidationError("A lookup id is required.")

        verdict = AIRating.parse(rating)
        if verdict is AIRating.NONE:
            await self._feedback.delete_ai(user_id, key, sense_index)
            return None

        provenance = await self._feedback.lookup_provenance(key)
        # ``sense_count`` of 0 means "we could not tell" — an entry we no longer
        # hold, or a payload at a schema version we no longer parse — and an
        # unanswerable question is not a failed one.
        known_senses = provenance.sense_count if provenance else 0
        if sense_index < 0 or (known_senses and sense_index >= known_senses):
            raise ValidationError("That sense is not part of this lookup.")

        return await self._feedback.upsert_ai(
            AIFeedback(
                user_id=user_id,
                lookup_id=key,
                sense_index=sense_index,
                rating=verdict,
                # A reason on a thumbs-*up* is dropped: the chips are only ever
                # offered under a thumbs-down, so one arriving here means a
                # client bug or a replayed request, and storing it would put
                # "the example was bad" next to a positive verdict.
                reason=AIFeedbackReason.parse(reason) if verdict is AIRating.DOWN else None,
                term=provenance.term if provenance else "",
                native_language=provenance.native_language if provenance else "",
                # Prefer what actually produced the deck over what this process
                # happens to be configured with now. They differ exactly when a
                # deploy landed between the lookup and the thumb, and the older
                # answer is the true one.
                prompt_version=provenance.prompt_version if provenance else self._prompt_version,
                provider=provenance.provider if provenance else self._provider,
                model=provenance.model if provenance else self._model,
            )
        )

    # ── the admin read surface ───────────────────────────────
    async def list_reports(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        kind: str | None = None,
    ) -> tuple[list[FeedbackReport], int]:
        """A page of reports, newest first, and the total behind it."""
        page = max(1, min(limit, MAX_PAGE_SIZE))
        wanted = FeedbackKind.parse(kind) if kind else None
        reports = await self._feedback.list_reports(limit=page, offset=max(0, offset), kind=wanted)
        return reports, await self._feedback.count_reports(kind=wanted)

    async def ai_scores(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[list[AISenseScore], AIFeedbackTotals]:
        """The rated card backs, worst first, and the overall up/down split."""
        page = max(1, min(limit, MAX_PAGE_SIZE))
        scores = await self._feedback.ai_sense_scores(limit=page, offset=max(0, offset))
        return scores, await self._feedback.ai_totals()
