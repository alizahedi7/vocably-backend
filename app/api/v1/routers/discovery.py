"""Explore, person-to-person shares, and friends."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    CurrentUser,
    DeckDiscoveryServiceDep,
    FriendServiceDep,
    enforce_share_limit,
)
from app.api.v1.schemas.deck import DeckOut
from app.api.v1.schemas.discovery import (
    AddFriendIn,
    FriendOut,
    FriendRequestOut,
    FriendRequestsOut,
    FriendsOut,
    PendingShareOut,
    PendingSharesOut,
    PublicDeckDetailOut,
    PublicDeckOut,
    PublicDecksOut,
    PublicWordOut,
    SharedDeckOut,
    SharedDecksOut,
    ShareDeckIn,
    ShareDeckOut,
)
from app.application.services.deck_discovery_service import MAX_PREVIEW_WORD_LIMIT

decks_router = APIRouter(prefix="/decks", tags=["discovery"])
friends_router = APIRouter(prefix="/users/me/friends", tags=["friends"])


# ── Explore ──────────────────────────────────────────────────
@decks_router.get("/public", response_model=PublicDecksOut)
async def list_public_decks(
    current_user: CurrentUser,
    discovery: DeckDiscoveryServiceDep,
    category: Annotated[str | None, Query(max_length=32)] = None,
    q: Annotated[str | None, Query(max_length=80, description="Name or author handle")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PublicDecksOut:
    views = await discovery.list_public(
        viewer_id=current_user.id, category=category, query=q, limit=limit, offset=offset
    )
    return PublicDecksOut(decks=[PublicDeckOut.from_view(v) for v in views])


@decks_router.get("/public/{deck_id}", response_model=PublicDeckDetailOut)
async def get_public_deck(
    deck_id: UUID,
    current_user: CurrentUser,
    discovery: DeckDiscoveryServiceDep,
) -> PublicDeckDetailOut:
    """A published deck and its sections — what someone reads before saving.

    Saving a five-hundred-word deck is a decision about the next month of
    someone's studying, and until this existed the only way to see what was in
    one was to take it. Sections only; the cards come from the endpoint below,
    so opening a preview does not fetch a coursebook.
    """
    view = await discovery.get_public(deck_id, current_user.id)
    units = await discovery.list_public_units(deck_id, current_user.id)
    return PublicDeckDetailOut.from_views(view, units)


@decks_router.get("/public/{deck_id}/words", response_model=list[PublicWordOut])
async def list_public_deck_words(
    deck_id: UUID,
    current_user: CurrentUser,
    discovery: DeckDiscoveryServiceDep,
    unit_id: Annotated[UUID | None, Query(description="Only this section's cards")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PREVIEW_WORD_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PublicWordOut]:
    """A page of a published deck's cards, oldest first.

    A bare array, like ``GET /words``, and paged the same way — the client asks
    again until a short page comes back, so a deck's size is never a constant
    anyone has to keep in step.
    """
    words = await discovery.list_public_words(
        deck_id, current_user.id, unit_id=unit_id, limit=limit, offset=offset
    )
    return [PublicWordOut.model_validate(w) for w in words]


@decks_router.post("/public/{deck_id}/import", response_model=DeckOut)
async def import_public_deck(
    deck_id: UUID,
    current_user: CurrentUser,
    discovery: DeckDiscoveryServiceDep,
) -> DeckOut:
    """Take a **copy**. Editing it cannot change anyone else's."""
    return DeckOut.model_validate(await discovery.import_public(deck_id, current_user.id))


# ── person-to-person ─────────────────────────────────────────
@decks_router.get("/shared", response_model=SharedDecksOut)
async def list_shared_decks(
    current_user: CurrentUser,
    discovery: DeckDiscoveryServiceDep,
) -> SharedDecksOut:
    views = await discovery.list_shared_with(current_user.id)
    return SharedDecksOut(decks=[SharedDeckOut.from_view(v) for v in views])


@decks_router.post("/shared/{share_id}/accept", response_model=DeckOut)
async def accept_shared_deck(
    share_id: UUID,
    current_user: CurrentUser,
    discovery: DeckDiscoveryServiceDep,
) -> DeckOut:
    """Take a deck someone sent. The **same** deck, with your own progress."""
    return DeckOut.model_validate(await discovery.accept(share_id, current_user.id))


@decks_router.delete("/shared/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def decline_shared_deck(
    share_id: UUID,
    current_user: CurrentUser,
    discovery: DeckDiscoveryServiceDep,
) -> None:
    """Stop being offered it. The sender is not told."""
    await discovery.decline(share_id, current_user.id)


@decks_router.get("/{deck_id}/shares", response_model=PendingSharesOut)
async def list_pending_shares(
    deck_id: UUID,
    current_user: CurrentUser,
    discovery: DeckDiscoveryServiceDep,
) -> PendingSharesOut:
    """Offers of this deck nobody has answered yet.

    The sender's half of the share: the roster says who is *in* the deck, and
    this says who has been asked. Accepting moves someone from one list to the
    other; declining removes them from both, and the sender is not told.
    """
    views = await discovery.list_pending_shares(deck_id, current_user.id)
    return PendingSharesOut(shares=[PendingShareOut.from_view(v) for v in views])


@decks_router.post(
    "/{deck_id}/share",
    response_model=ShareDeckOut,
    dependencies=[Depends(enforce_share_limit)],
)
async def share_deck(
    deck_id: UUID,
    payload: ShareDeckIn,
    current_user: CurrentUser,
    discovery: DeckDiscoveryServiceDep,
) -> ShareDeckOut:
    code = await discovery.share(
        deck_id,
        current_user.id,
        to_username=payload.to_username,
        role=payload.role,
    )
    return ShareDeckOut(code=code)


# ── friends ──────────────────────────────────────────────────
@friends_router.get("", response_model=FriendsOut)
async def list_friends(current_user: CurrentUser, friends: FriendServiceDep) -> FriendsOut:
    views = await friends.list_friends(current_user.id)
    return FriendsOut(friends=[FriendOut.from_view(v) for v in views])


@friends_router.post(
    "", response_model=FriendRequestOut, dependencies=[Depends(enforce_share_limit)]
)
async def add_friend(
    payload: AddFriendIn,
    current_user: CurrentUser,
    friends: FriendServiceDep,
) -> FriendRequestOut:
    """**Ask** to add someone by handle. Nothing joins their list until they agree.

    This used to write the friendship outright, which was defensible while the
    list was a by-product of deck shares — the sharer had typed the handle, so
    nothing was revealed — and stopped being so when handles became findable.
    The person on the other end was never told, could not refuse, and could not
    see it had happened.

    The response is the *request*, not a friend: it says what was sent, and
    deliberately says nothing about whether the recipient has seen it.
    """
    return FriendRequestOut.from_view(await friends.add(current_user.id, username=payload.username))


@friends_router.get("/requests", response_model=FriendRequestsOut)
async def list_friend_requests(
    current_user: CurrentUser, friends: FriendServiceDep
) -> FriendRequestsOut:
    """Who has asked to add you and is waiting.

    Declared before ``/{username}`` so the literal path wins the match, exactly
    as ``/decks/public`` and ``/decks/shared`` do on the deck router.
    """
    views = await friends.list_requests(current_user.id)
    return FriendRequestsOut(requests=[FriendRequestOut.from_view(v) for v in views])


@friends_router.post("/requests/{username}/accept", response_model=FriendOut)
async def accept_friend_request(
    username: str,
    current_user: CurrentUser,
    friends: FriendServiceDep,
) -> FriendOut:
    """Agree. Both people hold the friendship from here."""
    return FriendOut.from_view(await friends.accept(current_user.id, username=username))


@friends_router.delete("/requests/{username}", status_code=status.HTTP_204_NO_CONTENT)
async def decline_friend_request(
    username: str,
    current_user: CurrentUser,
    friends: FriendServiceDep,
) -> None:
    """Refuse. The sender is not told, and may ask again."""
    await friends.decline(current_user.id, username=username)


@friends_router.delete("/{username}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_friend(
    username: str,
    current_user: CurrentUser,
    friends: FriendServiceDep,
) -> None:
    """Remove someone, from both lists — and the way to take back a request."""
    await friends.remove(current_user.id, username=username)
