"""Feedback endpoint: what a learner writes to us from Settings.

The AI rating loop lives on the ``/ai`` router instead, beside the lookup it
rates — ``POST /ai/feedback`` is the other half of ``POST /ai/lookup``, and a
client that has one has the other.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, FeedbackServiceDep, enforce_feedback_limit
from app.api.v1.schemas.feedback import FeedbackReportIn, FeedbackReportOut

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post(
    "/report",
    response_model=FeedbackReportOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_feedback_limit)],
)
async def submit_report(
    payload: FeedbackReportIn,
    current_user: CurrentUser,
    feedback: FeedbackServiceDep,
) -> FeedbackReportOut:
    """Store a bug report, an idea, or anything else a learner wants to say.

    Rate-limited per user: this is the one endpoint in the app that writes
    free text of unbounded quantity into the database on request.

    Answers 201 as soon as the report is stored. Whether anyone was *told* about
    it is deliberately not part of that answer — see ``FeedbackNotifier``.
    """
    report = await feedback.submit_report(
        current_user.id,
        kind=payload.kind,
        message=payload.message,
        app_version=payload.app_version,
        platform=payload.platform,
        os_version=payload.os_version,
        locale=payload.locale,
    )
    return FeedbackReportOut.from_entity(report)
