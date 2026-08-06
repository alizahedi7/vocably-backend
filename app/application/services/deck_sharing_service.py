"""Sharing a deck: members, roles, the invite link, and the roster.

Two ways in — invite a person by handle, or hand out a link — and one rule
underneath both: the deck is *the same deck*, not a copy. A word an editor adds
is a word every member sees, while each member keeps their own boxes against
it. That is what section 1's ``words``/``word_progress`` split bought.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.services.deck_access import DeckAccess
from app.core.exceptions import AlreadyExistsError, NotFoundError, ValidationError
from app.domain.entities.deck_invite import DeckInvite
from app.domain.entities.deck_member import DeckMember
from app.domain.entities.user import User
from app.domain.enums import DeckRole
from app.domain.repositories.deck_activity_repository import (
    DeckActivityRepository,
    MemberTotals,
    MemberWeek,
)
from app.domain.repositories.deck_invite_repository import DeckInviteRepository
from app.domain.repositories.deck_member_repository import DeckMemberRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.services.calendar import week_start_for


@dataclass(frozen=True, slots=True)
class MemberProgressView:
    seen: int
    learning: int
    mastered: int
    reviewed_this_week: int
    mastered_this_week: int
    last_active_at: datetime | None


@dataclass(frozen=True, slots=True)
class MemberView:
    username: str
    name: str
    role: DeckRole
    joined_at: datetime
    is_me: bool
    #: ``None`` on the membership endpoint and filled on the roster — the
    #: client tolerates both, because the roster is cheap and the progress is not.
    progress: MemberProgressView | None = None


@dataclass(frozen=True, slots=True)
class MembershipView:
    deck_id: UUID
    my_role: DeckRole
    members: list[MemberView]
    invite_code: str
    invite_role: DeckRole
    invite_open: bool


class DeckSharingService:
    def __init__(
        self,
        members: DeckMemberRepository,
        invites: DeckInviteRepository,
        users: UserRepository,
        activity: DeckActivityRepository,
    ) -> None:
        self._members = members
        self._invites = invites
        self._users = users
        self._activity = activity
        self._access = DeckAccess(members)

    # ── reads ────────────────────────────────────────────────
    async def membership(self, deck_id: UUID, user_id: UUID) -> MembershipView:
        me = await self._access.require_read(deck_id, user_id)
        return await self._build(deck_id, me, with_progress=False)

    async def roster(self, deck_id: UUID, user_id: UUID) -> list[MemberView]:
        """Every member with their numbers filled in.

        Any member may read the roster — a class needs to see itself, and the
        weekly ranking is the point. What a non-owner does *not* get is another
        member's word-level detail, which no endpoint exposes at all.
        """
        me = await self._access.require_read(deck_id, user_id)
        view = await self._build(deck_id, me, with_progress=True)
        return view.members

    async def _build(self, deck_id: UUID, me: DeckMember, *, with_progress: bool) -> MembershipView:
        members = await self._members.list_for_deck(deck_id)
        invite = await self._invites.get_for_deck(deck_id)

        # One query for every member's name and handle, not one per member:
        # a class of thirty behind a single screen is where this feature would
        # otherwise become thirty round trips.
        users = await self._users.list_by_ids([m.user_id for m in members])

        totals: dict[UUID, MemberTotals] = {}
        weeks: dict[UUID, MemberWeek] = {}
        if with_progress:
            # Two more grouped queries for the whole roster, not two per member.
            totals = {t.user_id: t for t in await self._activity.totals_for_deck(deck_id)}
            viewer = users.get(me.user_id)
            since = week_start_for(viewer.timezone if viewer else None)
            weeks = {w.user_id: w for w in await self._activity.week_for_deck(deck_id, since)}

        views: list[MemberView] = []
        for member in members:
            user = users.get(member.user_id)
            if user is None:  # pragma: no cover — membership CASCADEs with the user
                continue
            progress = None
            if with_progress:
                total = totals.get(member.user_id)
                week = weeks.get(member.user_id)
                progress = MemberProgressView(
                    seen=total.seen if total else 0,
                    learning=total.learning if total else 0,
                    mastered=total.mastered if total else 0,
                    reviewed_this_week=week.reviews if week else 0,
                    mastered_this_week=week.mastered if week else 0,
                    last_active_at=total.last_active_at if total else None,
                )
            views.append(
                MemberView(
                    # A member with no handle cannot be addressed by the roster;
                    # the backfill means this only happens to an account created
                    # between the two migrations.
                    username=user.username or "",
                    name=user.name,
                    role=member.role,
                    joined_at=member.joined_at,
                    is_me=member.user_id == me.user_id,
                    progress=progress,
                )
            )

        return MembershipView(
            deck_id=deck_id,
            my_role=me.role,
            members=views,
            invite_code=invite.code if invite else "",
            invite_role=invite.role if invite else DeckRole.VIEWER,
            invite_open=bool(invite and invite.accepts(datetime.now(UTC))),
        )

    # ── members ──────────────────────────────────────────────
    async def add_member(
        self, deck_id: UUID, user_id: UUID, *, username: str, role: DeckRole
    ) -> MembershipView:
        me = await self._access.require_manage(deck_id, user_id)
        invitee = await self._resolve_handle(username, actor_id=user_id)

        # One statement decides it: a pre-check would let two identical invites
        # race past each other and turn the loser into a 500 on the primary key.
        added = await self._members.add_if_absent(
            DeckMember(
                deck_id=deck_id,
                user_id=invitee.id,
                # Never owner: there is exactly one, and transferring ownership
                # is a separate action.
                role=DeckRole.VIEWER if role is DeckRole.OWNER else role,
                invited_by_user_id=user_id,
            )
        )
        if not added:
            raise AlreadyExistsError("They already have this deck")
        return await self._build(deck_id, me, with_progress=False)

    async def change_role(
        self, deck_id: UUID, user_id: UUID, *, username: str, role: DeckRole
    ) -> MembershipView:
        me = await self._access.require_manage(deck_id, user_id)
        member = await self._member_by_handle(deck_id, username)
        if member.is_owner:
            # Demoting the owner would orphan the deck; ownership transfer is a
            # separate action and deliberately out of scope for now.
            raise ValidationError("The deck's owner cannot change their own role.")
        member.role = DeckRole.VIEWER if role is DeckRole.OWNER else role
        await self._members.update(member)
        return await self._build(deck_id, me, with_progress=False)

    async def remove_member(self, deck_id: UUID, user_id: UUID, *, username: str) -> MembershipView:
        me = await self._access.require_manage(deck_id, user_id)
        member = await self._member_by_handle(deck_id, username)
        if member.is_owner:
            raise ValidationError("The deck's owner cannot be removed.")
        # Their word_progress rows are deliberately left behind: rejoining a
        # class restores the boxes, and every aggregate is scoped through
        # deck_members anyway, so the rows are invisible until then.
        await self._members.remove(deck_id, member.user_id)
        return await self._build(deck_id, me, with_progress=False)

    # ── the invite link ──────────────────────────────────────
    async def open_invite(self, deck_id: UUID, user_id: UUID, *, role: DeckRole) -> MembershipView:
        me = await self._access.require_manage(deck_id, user_id)
        existing = await self._invites.get_for_deck(deck_id)
        invite = existing or DeckInvite(deck_id=deck_id, created_by_user_id=user_id)
        invite.role = DeckRole.VIEWER if role is DeckRole.OWNER else role
        invite.is_open = True
        await self._invites.upsert(invite)
        return await self._build(deck_id, me, with_progress=False)

    async def close_invite(self, deck_id: UUID, user_id: UUID) -> MembershipView:
        me = await self._access.require_manage(deck_id, user_id)
        invite = await self._invites.get_for_deck(deck_id)
        if invite is not None:
            invite.is_open = False
            await self._invites.upsert(invite)
        # Members already in stay in: revoking a link is not dissolving a class.
        return await self._build(deck_id, me, with_progress=False)

    async def join(self, user_id: UUID, *, code: str) -> UUID:
        """Redeem an invite code, returning the deck joined.

        Idempotent: joining twice is the same deck, not a conflict. A learner
        who taps a link twice has not done anything wrong.
        """
        invite = await self._invites.get_by_code(code.strip().upper())
        if invite is None or not invite.accepts(datetime.now(UTC)):
            # One message for "no such code", "closed" and "expired": telling
            # them apart tells someone guessing which codes exist.
            raise NotFoundError("That code does not match an open invite")

        # Idempotent in one statement: two taps on a link arrive together often
        # enough that check-then-insert would show a learner a 500 for doing
        # nothing wrong.
        await self._members.add_if_absent(
            DeckMember(
                deck_id=invite.deck_id,
                user_id=user_id,
                role=invite.role,
                invited_by_user_id=invite.created_by_user_id,
            )
        )
        return invite.deck_id

    # ── helpers ──────────────────────────────────────────────
    async def _resolve_handle(self, username: str, *, actor_id: UUID) -> User:
        handle = username.strip().lower()
        if not handle:
            raise ValidationError("Enter a handle first")
        user = await self._users.get_by_username(handle)
        if user is None:
            raise ValidationError("No one uses that handle")
        if user.id == actor_id:
            raise ValidationError("That is your own handle")
        return user

    async def _member_by_handle(self, deck_id: UUID, username: str) -> DeckMember:
        user = await self._users.get_by_username(username.strip().lower())
        member = None if user is None else await self._members.get(deck_id, user.id)
        if member is None:
            raise NotFoundError("They are not a member of this deck.")
        return member
