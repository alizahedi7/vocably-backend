"""Port: telling somebody a report arrived.

Today nothing is told: reports land in Postgres and are read from the admin
dashboard, which is the surface that already exists and the one that cannot page
anybody at 3am about a typo. This port is here so that decision stays a
*configuration* decision — a webhook, an email, a ticket in a tracker — instead
of an edit to :class:`~app.application.services.feedback_service.FeedbackService`.

Two rules any real adapter must keep, both of which the null one keeps trivially:

* **Notifying must never fail the submit.** The learner's report is stored the
  moment the transaction commits; a channel that is down is our problem, not
  theirs, and the endpoint must still answer 201. The service therefore swallows
  and logs whatever this raises.
* **It must not block the response.** A real adapter belongs on the Celery
  ``default`` queue — hand it the report id and let the worker read the row —
  rather than doing network I/O inside the request. See ``app/tasks/`` for the
  shape; ``phonetics.py`` is the closest example.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.entities.feedback import FeedbackReport


class FeedbackNotifier(ABC):
    @abstractmethod
    async def notify_report(self, report: FeedbackReport) -> None:
        """Announce that ``report`` has been stored."""


class NullFeedbackNotifier(FeedbackNotifier):
    """Announces nothing. The only implementation there is, on purpose."""

    async def notify_report(self, report: FeedbackReport) -> None:
        return None
