"""SQLAlchemy ORM models.

Importing this package registers every model on ``Base.metadata`` so Alembic autogenerate
and ``create_all`` can see them.
"""

from app.infrastructure.db.models.ai_lookup import AILookupAliasModel, AILookupEntryModel
from app.infrastructure.db.models.daily_deck_activity import DailyDeckActivityModel
from app.infrastructure.db.models.deck import DeckModel
from app.infrastructure.db.models.deck_build import DeckBuildItemModel, DeckBuildJobModel
from app.infrastructure.db.models.deck_invite import DeckInviteModel
from app.infrastructure.db.models.deck_member import DeckMemberModel
from app.infrastructure.db.models.deck_share import DeckShareModel
from app.infrastructure.db.models.deck_unit import DeckUnitModel
from app.infrastructure.db.models.friend_link import FriendLinkModel
from app.infrastructure.db.models.lexicon import (
    LexemeModel,
    LexemeSenseModel,
    LexemeSenseTranslationModel,
)
from app.infrastructure.db.models.otp_challenge import OtpChallengeModel
from app.infrastructure.db.models.user import UserModel
from app.infrastructure.db.models.word import WordModel
from app.infrastructure.db.models.word_progress import WordProgressModel
from app.infrastructure.db.models.word_review import WordReviewModel
from app.infrastructure.db.models.xp_event import XpEventModel

__all__ = [
    "AILookupAliasModel",
    "AILookupEntryModel",
    "DailyDeckActivityModel",
    "DeckBuildItemModel",
    "DeckBuildJobModel",
    "DeckInviteModel",
    "DeckMemberModel",
    "DeckShareModel",
    "DeckModel",
    "DeckUnitModel",
    "FriendLinkModel",
    "LexemeModel",
    "LexemeSenseModel",
    "LexemeSenseTranslationModel",
    "OtpChallengeModel",
    "UserModel",
    "WordModel",
    "WordProgressModel",
    "WordReviewModel",
    "XpEventModel",
]
