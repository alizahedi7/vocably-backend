"""SQLAlchemy implementation of :class:`OTPChallengeRepository`."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.otp_challenge import OtpChallenge
from app.domain.repositories.otp_repository import OTPChallengeRepository
from app.infrastructure.db import mappers
from app.infrastructure.db.models.otp_challenge import OtpChallengeModel


class SqlAlchemyOTPChallengeRepository(OTPChallengeRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_by_phone(self, phone: str) -> OtpChallenge | None:
        stmt = (
            select(OtpChallengeModel)
            .where(
                OtpChallengeModel.phone == phone,
                OtpChallengeModel.consumed.is_(False),
            )
            .order_by(OtpChallengeModel.created_at.desc())
            .limit(1)
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return mappers.otp_to_entity(model) if model else None

    async def add(self, challenge: OtpChallenge) -> OtpChallenge:
        model = OtpChallengeModel(id=challenge.id)
        mappers.apply_otp(challenge, model)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.otp_to_entity(model)

    async def update(self, challenge: OtpChallenge) -> OtpChallenge:
        model = await self._session.get(OtpChallengeModel, challenge.id)
        if model is None:
            raise ValueError(f"OTP challenge {challenge.id} does not exist")
        mappers.apply_otp(challenge, model)
        await self._session.flush()
        await self._session.refresh(model)
        return mappers.otp_to_entity(model)

    async def invalidate_for_phone(self, phone: str) -> None:
        await self._session.execute(
            update(OtpChallengeModel)
            .where(
                OtpChallengeModel.phone == phone,
                OtpChallengeModel.consumed.is_(False),
            )
            .values(consumed=True)
        )
