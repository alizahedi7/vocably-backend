"""SQLAlchemy ORM models.

Importing this package registers every model on ``Base.metadata`` so Alembic autogenerate
and ``create_all`` can see them.
"""

from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.otp_challenge import OtpChallengeModel
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.models.word import WordModel

__all__ = ["DeckModel", "OtpChallengeModel", "UserModel", "WordModel"]
