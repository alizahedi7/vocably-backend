"""SQLAlchemy ORM models.

Importing this package registers every model on ``Base.metadata`` so Alembic autogenerate
and ``create_all`` can see them.
"""

from app.infrastructure.db.models.ai_lookup import AILookupAliasModel, AILookupEntryModel
from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.deck_member import DeckMemberModel
from app.infrastructure.db.models.otp_challenge import OtpChallengeModel
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.models.word import WordModel
from app.infrastructure.db.models.word_progress import WordProgressModel
from app.infrastructure.db.models.word_review import WordReviewModel

__all__ = [
    "AILookupAliasModel",
    "AILookupEntryModel",
    "DeckMemberModel",
    "DeckModel",
    "OtpChallengeModel",
    "UserModel",
    "WordModel",
    "WordProgressModel",
    "WordReviewModel",
]
