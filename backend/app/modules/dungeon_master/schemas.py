"""Pydantic request/response schemas for the dungeon_master module."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Stat Schema ───────────────────────────────────────────────────────────────


class StatType(str, Enum):
    number = "number"
    text = "text"
    enum = "enum"


class StatDefinition(BaseModel):
    name: str = Field(pattern=r"^[a-z_][a-z0-9_]*$", description="Snake_case stat identifier")
    display_name: str
    type: StatType = StatType.number
    initial_value: Any = 0
    min: float | None = None
    max: float | None = None
    options: list[str] | None = None  # required when type == enum


# ── Game Rulesets ─────────────────────────────────────────────────────────────


class CreateRulesetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    stat_schema: list[StatDefinition] = Field(default_factory=list)
    rules_text: str | None = None
    recommended_model: str | None = None
    is_public: bool = False


class UpdateRulesetRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    stat_schema: list[StatDefinition] | None = None
    rules_text: str | None = None
    recommended_model: str | None = None
    is_public: bool | None = None


class RulesetResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    stat_schema: list[dict[str, Any]]
    rules_text: str | None
    recommended_model: str | None
    is_public: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── DM Sessions ───────────────────────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    chat_id: uuid.UUID
    ruleset_id: uuid.UUID


class SessionResponse(BaseModel):
    id: uuid.UUID
    chat_id: uuid.UUID
    ruleset_id: uuid.UUID
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Character Sheets ──────────────────────────────────────────────────────────


class CharacterSheetResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    character_id: uuid.UUID | None
    is_player: bool
    display_name: str | None
    stats: dict[str, Any]
    inventory: list[Any]
    skills: dict[str, Any]
    updated_at: datetime

    model_config = {"from_attributes": True}


class UpdateSheetRequest(BaseModel):
    stats: dict[str, Any] | None = None
    inventory: list[Any] | None = None
    skills: dict[str, Any] | None = None
    display_name: str | None = None


class CreateSheetRequest(BaseModel):
    character_id: uuid.UUID | None = None
    is_player: bool = True
    display_name: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    inventory: list[Any] = Field(default_factory=list)
    skills: dict[str, Any] = Field(default_factory=dict)


# ── Dice Rolls ────────────────────────────────────────────────────────────────


class RollRequest(BaseModel):
    expression: str = Field(min_length=1, max_length=50)
    context: str | None = None


class RollResponse(BaseModel):
    id: uuid.UUID
    expression: str
    dice_faces: str
    num_dice: int
    modifier: int
    all_rolls: list[int]
    kept_rolls: list[int]
    total: int
    context: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RollListResponse(BaseModel):
    rolls: list[RollResponse]
    total: int


# ── DM Evaluation ─────────────────────────────────────────────────────────────


class EvaluateRequest(BaseModel):
    player_action: str = Field(min_length=1, max_length=4000)
    recent_messages: list[dict[str, str]] = Field(
        default_factory=list,
        description="Last N chat messages as [{role, content}] pairs",
    )
    character_id: uuid.UUID | None = None


class DMVerdict(str, Enum):
    continue_ = "continue"
    reject = "reject"
    ask = "ask"


class StatChangeSummary(BaseModel):
    name: str
    display_name: str
    old_value: Any
    new_value: Any
    delta: Any | None


class DMEvalResult(BaseModel):
    verdict: DMVerdict
    summary: str | None = None
    narrative_hint: str | None = None
    rejection_reason: str | None = None
    ask_question: str | None = None
    stat_changes: list[StatChangeSummary] = Field(default_factory=list)
    roll_ids: list[uuid.UUID] = Field(default_factory=list)
    death_state: bool = False
