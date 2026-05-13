"""Database layer.

Importing this package registers every ORM model on ``Base.metadata`` so
Alembic autogenerate and the test fixture's ``Base.metadata.create_all`` see
the full schema regardless of which module triggered the import.
"""

from app.db.base import Base, SoftDeleteMixin, TimestampMixin  # noqa: F401

# Import every models module so its tables register on Base.metadata.
from app.modules.auth import models as _auth_models  # noqa: F401
from app.modules.users import models as _users_models  # noqa: F401
from app.modules.characters import models as _character_models  # noqa: F401
from app.modules.chats import models as _chat_models  # noqa: F401
from app.modules.messages import models as _message_models  # noqa: F401
from app.modules.dungeon_master import models as _dm_models  # noqa: F401
from app.modules.images import models as _image_models  # noqa: F401

__all__ = ["Base", "SoftDeleteMixin", "TimestampMixin"]
