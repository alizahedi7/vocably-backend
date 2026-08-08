"""Deck-sharing request/response schemas.

Field names here are the contract with ``lib/models/deck_member.dart``; that
file is the authoritative parser. Renaming one is a breaking change for the
sharing and roster screens.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.application.services.deck_sharing_service import (
    MemberProgressView,
    MembershipView,
    MemberView,
)
from app.domain.enums import DeckRole


class MemberProgressOut(BaseModel):
    seen: int
    learning: int
    mastered: int
    reviewed_this_week: int
    mastered_this_week: int
    last_active_at: datetime | None

    @classmethod
    def from_view(cls, view: MemberProgressView) -> MemberProgressOut:
        return cls(
            seen=view.seen,
            learning=view.learning,
            mastered=view.mastered,
            reviewed_this_week=view.reviewed_this_week,
            mastered_this_week=view.mastered_this_week,
            last_active_at=view.last_active_at,
        )


class DeckMemberOut(BaseModel):
    username: str
    name: str
    role: DeckRole
    joined_at: datetime
    is_me: bool
    #: Null on the membership endpoint, filled on the roster. The client
    #: tolerates both — "the roster is cheap, the progress is not".
    progress: MemberProgressOut | None = None

    @classmethod
    def from_view(cls, view: MemberView) -> DeckMemberOut:
        return cls(
            username=view.username,
            name=view.name,
            role=view.role,
            joined_at=view.joined_at,
            is_me=view.is_me,
            progress=None if view.progress is None else MemberProgressOut.from_view(view.progress),
        )


class DeckMembershipOut(BaseModel):
    deck_id: UUID
    my_role: DeckRole
    members: list[DeckMemberOut]
    invite_code: str
    invite_role: DeckRole
    invite_open: bool

    @classmethod
    def from_view(cls, view: MembershipView) -> DeckMembershipOut:
        return cls(
            deck_id=view.deck_id,
            my_role=view.my_role,
            members=[DeckMemberOut.from_view(m) for m in view.members],
            invite_code=view.invite_code,
            invite_role=view.invite_role,
            invite_open=view.invite_open,
        )


class RosterOut(BaseModel):
    members: list[DeckMemberOut]


class AddMemberIn(BaseModel):
    username: str = Field(max_length=20)
    #: Viewer by default — the safe answer for a class.
    role: DeckRole = DeckRole.VIEWER


class ChangeRoleIn(BaseModel):
    role: DeckRole = DeckRole.VIEWER


class OpenInviteIn(BaseModel):
    role: DeckRole = DeckRole.VIEWER


class JoinIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class JoinOut(BaseModel):
    deck_id: UUID
