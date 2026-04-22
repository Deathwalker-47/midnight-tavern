"""Dungeon Master module database models.

FK columns referencing users/chats/characters are nullable until those
tables are created in their respective foundation tasks. The FK constraints
will be added via Alembic migrations once the referenced tables exist.
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin


class DMActionType(str, PyEnum):
    roll = "roll"
    stat_update = "stat_update"
    inventory_update = "inventory_update"
    skill_update = "skill_update"
    submit = "submit"
    ask_player = "ask_player"
    reject = "reject"


class GameRuleset(Base, TimestampMixin, SoftDeleteMixin):
    """Reusable game ruleset created by a user.

    The stat_schema is a JSON array of stat definitions that drives
    dynamic tool generation for the DM agent:
      [{"name": "hp", "display_name": "Hit Points", "type": "number",
        "min": 0, "max": 100, "initial_value": 100}, ...]
    """

    __tablename__ = "game_rulesets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Will gain FK(users.id) once users table exists
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stat_schema: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    rules_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    sessions: Mapped[list["DMSession"]] = relationship(
        "DMSession", back_populates="ruleset"
    )


class DMSession(Base, SoftDeleteMixin):
    """One DM session per chat. Opt-in — not all chats have DM mode."""

    __tablename__ = "dm_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Will gain FK(chats.id) once chats table exists
    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )
    ruleset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("game_rulesets.id"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ruleset: Mapped["GameRuleset"] = relationship(
        "GameRuleset", back_populates="sessions"
    )
    character_sheets: Mapped[list["CharacterSheet"]] = relationship(
        "CharacterSheet", back_populates="session"
    )
    rolls: Mapped[list["DMRoll"]] = relationship("DMRoll", back_populates="session")
    actions: Mapped[list["DMAction"]] = relationship(
        "DMAction", back_populates="session"
    )


class CharacterSheet(Base):
    """RPG stats, inventory, and skills for a character within a DM session.

    Both player characters and NPCs can have sheets. Stats are stored as JSONB
    so any ruleset schema can be accommodated without schema migrations.
    """

    __tablename__ = "character_sheets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dm_sessions.id"), nullable=False, index=True
    )
    # Will gain FK(characters.id) once characters table exists
    character_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    is_player: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    inventory: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    skills: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    session: Mapped["DMSession"] = relationship(
        "DMSession", back_populates="character_sheets"
    )


class DMRoll(Base):
    """Immutable audit log of every server-side dice roll.

    Never deleted — provides a tamper-proof history of all dice outcomes.
    """

    __tablename__ = "dm_rolls"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dm_sessions.id"), nullable=False, index=True
    )
    dice_expression: Mapped[str] = mapped_column(String(50), nullable=False)
    dice_faces: Mapped[str] = mapped_column(String(10), nullable=False)  # "20", "6", "F"
    num_dice: Mapped[int] = mapped_column(Integer, nullable=False)
    modifier: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    all_rolls: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    kept_rolls: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["DMSession"] = relationship("DMSession", back_populates="rolls")


class DMAction(Base):
    """Log of every DM agent tool call for a given message.

    Provides a full audit trail of mechanical decisions — what the DM
    decided, what changed, and why — linked to the triggering message.
    """

    __tablename__ = "dm_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dm_sessions.id"), nullable=False, index=True
    )
    # Will gain FK(messages.id) once messages table exists
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    action_type: Mapped[DMActionType] = mapped_column(
        Enum(DMActionType), nullable=False
    )
    action_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["DMSession"] = relationship(
        "DMSession", back_populates="actions"
    )
