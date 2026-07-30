"""Admin dashboard endpoints — read-only, platform-wide analytics.

Every route is gated by :data:`CurrentAdmin` (see ``require_admin``), so only
users with ``is_admin`` set may reach them. Responses use camelCase aliases to
match the ``vocably-admin`` dashboard's TypeScript types.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import AdminServiceDep, CurrentAdmin
from app.api.v1.schemas.admin import (
    AdminCacheAliasOut,
    AdminCacheAliasPageOut,
    AdminCacheEntryOut,
    AdminCacheEntryPageOut,
    AdminCacheOverviewOut,
    AdminCategoryOut,
    AdminUserOut,
    AdminWordOut,
    AuthMethodBreakdownOut,
    OverviewOut,
    TimeSeriesPointOut,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/overview", response_model=OverviewOut)
async def overview(_admin: CurrentAdmin, admin: AdminServiceDep) -> OverviewOut:
    return OverviewOut.from_dto(await admin.overview())


@router.get("/registrations", response_model=list[TimeSeriesPointOut])
async def registrations(
    _admin: CurrentAdmin,
    admin: AdminServiceDep,
    days: int = Query(default=30, ge=1, le=365),
) -> list[TimeSeriesPointOut]:
    return [TimeSeriesPointOut.from_dto(point) for point in await admin.registrations(days)]


@router.get("/auth-methods", response_model=list[AuthMethodBreakdownOut])
async def auth_methods(
    _admin: CurrentAdmin, admin: AdminServiceDep
) -> list[AuthMethodBreakdownOut]:
    return [AuthMethodBreakdownOut.from_dto(row) for row in await admin.auth_methods()]


@router.get("/users", response_model=list[AdminUserOut])
async def users(_admin: CurrentAdmin, admin: AdminServiceDep) -> list[AdminUserOut]:
    return [AdminUserOut.from_dto(row) for row in await admin.users()]


@router.get("/categories", response_model=list[AdminCategoryOut])
async def categories(_admin: CurrentAdmin, admin: AdminServiceDep) -> list[AdminCategoryOut]:
    return [AdminCategoryOut.from_dto(row) for row in await admin.categories()]


@router.get("/words", response_model=list[AdminWordOut])
async def words(_admin: CurrentAdmin, admin: AdminServiceDep) -> list[AdminWordOut]:
    return [AdminWordOut.from_dto(row) for row in await admin.words()]


@router.get("/cache/overview", response_model=AdminCacheOverviewOut)
async def cache_overview(_admin: CurrentAdmin, admin: AdminServiceDep) -> AdminCacheOverviewOut:
    return AdminCacheOverviewOut.from_dto(await admin.cache_overview())


@router.get("/cache/entries/{entry_id}", response_model=AdminCacheEntryOut)
async def cache_entry(
    entry_id: UUID, _admin: CurrentAdmin, admin: AdminServiceDep
) -> AdminCacheEntryOut:
    return AdminCacheEntryOut.from_dto(await admin.cache_entry(entry_id))


@router.get("/cache/entries", response_model=AdminCacheEntryPageOut)
async def cache_entries(
    _admin: CurrentAdmin,
    admin: AdminServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=255),
) -> AdminCacheEntryPageOut:
    rows, total = await admin.cache_entries(limit, offset, q)
    items = [AdminCacheEntryOut.from_dto(row) for row in rows]
    return AdminCacheEntryPageOut(items=items, total=total)


@router.get("/cache/entries/{entry_id}/aliases", response_model=AdminCacheAliasPageOut)
async def cache_entry_aliases(
    entry_id: UUID,
    _admin: CurrentAdmin,
    admin: AdminServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AdminCacheAliasPageOut:
    rows, total = await admin.cache_aliases(entry_id, limit, offset)
    return AdminCacheAliasPageOut(
        items=[AdminCacheAliasOut.from_dto(row) for row in rows], total=total
    )
