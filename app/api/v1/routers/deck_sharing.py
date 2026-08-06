"""Deck-sharing endpoints: members, roles, the invite link, and the roster."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DeckSharingServiceDep, enforce_join_limit
from app.api.v1.schemas.deck_sharing import (
    AddMemberIn,
    ChangeRoleIn,
    DeckMemberOut,
    DeckMembershipOut,
    JoinIn,
    JoinOut,
    OpenInviteIn,
    RosterOut,
)

router = APIRouter(prefix="/decks", tags=["sharing"])


@router.post("/join", response_model=JoinOut, dependencies=[Depends(enforce_join_limit)])
async def join_deck(
    payload: JoinIn,
    current_user: CurrentUser,
    sharing: DeckSharingServiceDep,
) -> JoinOut:
    """Redeem an invite code.

    Idempotent: joining twice answers 200 with the same deck, not a conflict —
    a learner who taps a link twice has not done anything wrong.

    Declared before this router's ``/{deck_id}/...`` routes so the literal
    "join" is never matched as a deck id.
    """
    deck_id = await sharing.join(current_user.id, code=payload.code)
    return JoinOut(deck_id=deck_id)


@router.get("/{deck_id}/membership", response_model=DeckMembershipOut)
async def get_membership(
    deck_id: UUID,
    current_user: CurrentUser,
    sharing: DeckSharingServiceDep,
) -> DeckMembershipOut:
    """The deck's sharing state. 404 when the caller is not a member — which
    the client treats as "not shared" rather than as an error."""
    return DeckMembershipOut.from_view(await sharing.membership(deck_id, current_user.id))


@router.get("/{deck_id}/roster", response_model=RosterOut)
async def get_roster(
    deck_id: UUID,
    current_user: CurrentUser,
    sharing: DeckSharingServiceDep,
) -> RosterOut:
    members = await sharing.roster(deck_id, current_user.id)
    return RosterOut(members=[DeckMemberOut.from_view(m) for m in members])


@router.post("/{deck_id}/members", response_model=DeckMembershipOut)
async def add_member(
    deck_id: UUID,
    payload: AddMemberIn,
    current_user: CurrentUser,
    sharing: DeckSharingServiceDep,
) -> DeckMembershipOut:
    view = await sharing.add_member(
        deck_id, current_user.id, username=payload.username, role=payload.role
    )
    return DeckMembershipOut.from_view(view)


@router.patch("/{deck_id}/members/{username}", response_model=DeckMembershipOut)
async def change_member_role(
    deck_id: UUID,
    username: str,
    payload: ChangeRoleIn,
    current_user: CurrentUser,
    sharing: DeckSharingServiceDep,
) -> DeckMembershipOut:
    view = await sharing.change_role(deck_id, current_user.id, username=username, role=payload.role)
    return DeckMembershipOut.from_view(view)


@router.delete("/{deck_id}/members/{username}", response_model=DeckMembershipOut)
async def remove_member(
    deck_id: UUID,
    username: str,
    current_user: CurrentUser,
    sharing: DeckSharingServiceDep,
) -> DeckMembershipOut:
    view = await sharing.remove_member(deck_id, current_user.id, username=username)
    return DeckMembershipOut.from_view(view)


@router.post("/{deck_id}/invite", response_model=DeckMembershipOut)
async def open_invite(
    deck_id: UUID,
    payload: OpenInviteIn,
    current_user: CurrentUser,
    sharing: DeckSharingServiceDep,
) -> DeckMembershipOut:
    """Open the link. Re-opening reuses the row, so a code already handed to a
    class keeps working."""
    view = await sharing.open_invite(deck_id, current_user.id, role=payload.role)
    return DeckMembershipOut.from_view(view)


@router.delete("/{deck_id}/invite", response_model=DeckMembershipOut)
async def close_invite(
    deck_id: UUID,
    current_user: CurrentUser,
    sharing: DeckSharingServiceDep,
) -> DeckMembershipOut:
    """Close the link. Members already in stay in."""
    return DeckMembershipOut.from_view(await sharing.close_invite(deck_id, current_user.id))
