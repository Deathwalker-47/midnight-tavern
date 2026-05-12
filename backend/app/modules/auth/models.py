"""User authentication model."""

import uuid

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    provider_keys: Mapped[list["ProviderKey"]] = relationship(  # type: ignore[name-defined]
        "ProviderKey", back_populates="user", lazy="select"
    )
    characters: Mapped[list["Character"]] = relationship(  # type: ignore[name-defined]
        "Character", back_populates="owner", lazy="select"
    )
    chats: Mapped[list["Chat"]] = relationship(  # type: ignore[name-defined]
        "Chat", back_populates="user", lazy="select"
    )
