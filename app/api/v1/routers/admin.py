"""Admin dashboard endpoints — read-only, platform-wide analytics.

Every route is gated by :data:`CurrentAdmin` (see ``require_admin``), so only
users with ``is_admin`` set may reach them. Responses use camelCase aliases to
match the ``vocably-admin`` dashboard's TypeScript types.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import AdminServiceDep, CurrentAdmin
from app.api.v1.schemas.admin import (
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
