"""Grant (or revoke) admin rights for an existing user.

Admin is deliberately not self-serve — there is no API endpoint that flips
``is_admin``, since that would be a privilege-escalation hole. Instead an
operator promotes a known account out of band with this script::

    make grant-admin who=+989121234567          # promote by phone
    make grant-admin who=someone@example.com     # promote by email
    make grant-admin who=+989121234567 revoke=1  # demote again

Equivalent without make: ``python -m app.scripts.grant_admin <identifier> [--revoke]``.

The identifier is matched against ``phone`` first, then ``email``. The command is
idempotent and exits non-zero if no matching user exists.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import or_, select

from app.core.database import async_session_factory
from app.core.logging import configure_logging, get_logger
from app.infrastructure.db.models.user import UserModel

logger = get_logger("vocably.grant_admin")


async def set_admin(identifier: str, *, is_admin: bool) -> int:
    configure_logging()
    async with async_session_factory() as session:
        user = (
            await session.execute(
                select(UserModel).where(
                    or_(UserModel.phone == identifier, UserModel.email == identifier)
                )
            )
        ).scalar_one_or_none()

        if user is None:
            logger.error("No user found with phone or email %r.", identifier)
            return 1

        if user.is_admin == is_admin:
            logger.info(
                "User %s (%s) is already %s — nothing to do.",
                user.id,
                identifier,
                "an admin" if is_admin else "a regular user",
            )
            return 0

        user.is_admin = is_admin
        await session.commit()
        logger.info(
            "%s admin rights for user %s (%s).",
            "Granted" if is_admin else "Revoked",
            user.id,
            identifier,
        )
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Grant or revoke admin rights for a user.")
    parser.add_argument("identifier", help="The user's phone number or email address.")
    parser.add_argument(
        "--revoke",
        action="store_true",
        help="Revoke admin rights instead of granting them.",
    )
    args = parser.parse_args()

    exit_code = asyncio.run(set_admin(args.identifier, is_admin=not args.revoke))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
