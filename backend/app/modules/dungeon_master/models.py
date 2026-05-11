"""Dungeon Master module database models."""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin

_DEFAULT_ENFORCEMENT: dict[str, Any] = {
    "require_skill_for_ability": True,
    "require_item_for_use": True,
    "permadeath": True,
    "no_invented_powers": True,
    "resource_costs_enforced": True,
    "dead_cannot_act": True,
    "respect_conditions": True,
}


class DMActionType(str, PyEnum):
    roll = "roll"
    stat_update = "stat_update"
    inventory_update = "inventory_update"
    skill_update = "skill_update"
    spell_update = "spell_update"
    description_update = "description_update"
    submit = "submit"
    ask_player = "ask_player"
    reject = "reject"


class GameRuleset(Base, TimestampMixin, SoftDeleteMixin):
    """Reusable game ruleset created by a user.

    stat_schema is a JSON array of stat definitions that drives dynamic tool
    generation for the DM agent:
      [{"name": "hp", "display_name": "Hit Points", "type": "number",
        "min": 0, "max": 100, "initial_value": 100, "description": "...",
        "is_resource_bar": true, "bar_color": "#22c55e"}, ...]
    """

    __tablename__ = "game_rulesets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stat_schema: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    rules_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reminder_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    player_guide: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recommended_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dm_temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)
    dm_max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)
    context_window: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    enforcement_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=lambda: dict(_DEFAULT_ENFORCEMENT)
    )
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    sessions: Mapped[list["DMSession"]] = relationship("DMSession", back_populates="ruleset")
    attachments: Mapped[list["DMConfigAttachment"]] = relationship("DMConfigAttachment", back_populates="ruleset")


class DMSession(Base, SoftDeleteMixin):
    """One DM session per chat. Opt-in — not all chats have DM mode."""

    __tablename__ = "dm_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    ruleset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("game_rulesets.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ruleset: Mapped["GameRuleset"] = relationship("GameRuleset", back_populates="sessions")
    character_sheets: Mapped[list["CharacterSheet"]] = relationship("CharacterSheet", back_populates="session")
    rolls: Mapped[list["DMRoll"]] = relationship("DMRoll", back_populates="session")
    actions: Mapped[list["DMAction"]] = relationship("DMAction", back_populates="session")
    events: Mapped[list["GameEvent"]] = relationship("GameEvent", back_populates="session")


class CharacterSheet(Base):
    """RPG stats, inventory, and skills for a character within a DM session."""

    __tablename__ = "character_sheets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dm_sessions.id"), nullable=False, index=True
    )
    character_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    is_player: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_alive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    inventory: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    skills: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    spells: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    session: Mapped["DMSession"] = relationship("DMSession", back_populates="character_sheets")


class DMRoll(Base):
    """Immutable audit log of every server-side dice roll."""

    __tablename__ = "dm_rolls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dm_sessions.id"), nullable=False, index=True
    )
    dice_expression: Mapped[str] = mapped_column(String(50), nullable=False)
    dice_faces: Mapped[str] = mapped_column(String(10), nullable=False)
    num_dice: Mapped[int] = mapped_column(Integer, nullable=False)
    modifier: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    all_rolls: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    kept_rolls: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    dc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    advantage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    disadvantage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stat_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["DMSession"] = relationship("DMSession", back_populates="rolls")


class DMAction(Base):
    """Log of every DM agent tool call for a given message."""

    __tablename__ = "dm_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dm_sessions.id"), nullable=False, index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    action_type: Mapped[DMActionType] = mapped_column(Enum(DMActionType), nullable=False)
    action_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["DMSession"] = relationship("DMSession", back_populates="actions")


class GameEvent(Base):
    """Comprehensive per-action ruling record. One row per player message processed by the DM."""

    __tablename__ = "game_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dm_sessions.id"), nullable=False, index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    player_action: Mapped[str] = mapped_column(Text, nullable=False)
    ruling: Mapped[str] = mapped_column(String(10), nullable=False)
    ruling_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    forbidden_elements: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    stat_changes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    inventory_changes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    skill_changes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    dm_model_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dm_prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dm_completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dm_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pre_validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    player_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["DMSession"] = relationship("DMSession", back_populates="events")


class DMConfigAttachment(Base):
    """Links a ruleset to a character card or directly to a specific chat."""

    __tablename__ = "dm_config_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ruleset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("game_rulesets.id"), nullable=False, index=True
    )
    character_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    chat_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="required")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ruleset: Mapped["GameRuleset"] = relationship("GameRuleset", back_populates="attachments")
