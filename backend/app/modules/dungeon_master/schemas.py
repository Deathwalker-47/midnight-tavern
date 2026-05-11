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
    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$", description="Stat identifier")
    display_name: str
    type: StatType = StatType.number
    initial_value: Any = 0
    min: float | None = None
    max: float | None = None
    options: list[str] | None = None  # required when type == enum
    description: str | None = None  # DM reads this to understand what the stat means
    is_resource_bar: bool = False
    bar_color: str | None = None  # hex color e.g. "#22c55e"


# ── Enforcement Config ────────────────────────────────────────────────────────


class EnforcementConfig(BaseModel):
    require_skill_for_ability: bool = True
    require_item_for_use: bool = True
    permadeath: bool = True
    no_invented_powers: bool = True
    resource_costs_enforced: bool = True
    dead_cannot_act: bool = True
    respect_conditions: bool = True


# ── Game Rulesets ─────────────────────────────────────────────────────────────


class CreateRulesetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    stat_schema: list[StatDefinition] = Field(default_factory=list)
    rules_text: str | None = None
    reminder_text: str | None = None
    instruction: str | None = None
    player_guide: str | None = None
    recommended_model: str | None = None
    recommended_provider: str | None = None
    dm_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    dm_max_tokens: int = Field(default=2000, ge=256, le=8192)
    context_window: int = Field(default=10, ge=1, le=50)
    enforcement_config: EnforcementConfig = Field(default_factory=EnforcementConfig)
    is_public: bool = False


class UpdateRulesetRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    stat_schema: list[StatDefinition] | None = None
    rules_text: str | None = None
    reminder_text: str | None = None
    instruction: str | None = None
    player_guide: str | None = None
    recommended_model: str | None = None
    recommended_provider: str | None = None
    dm_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    dm_max_tokens: int | None = Field(default=None, ge=256, le=8192)
    context_window: int | None = Field(default=None, ge=1, le=50)
    enforcement_config: EnforcementConfig | None = None
    is_public: bool | None = None


class RulesetResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    stat_schema: list[dict[str, Any]]
    rules_text: str | None
    reminder_text: str | None
    instruction: str | None
    player_guide: str | None
    recommended_model: str | None
    recommended_provider: str | None
    dm_temperature: float
    dm_max_tokens: int
    context_window: int
    enforcement_config: dict[str, Any]
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
    is_alive: bool
    display_name: str | None
    description: str | None
    stats: dict[str, Any]
    inventory: list[Any]
    skills: dict[str, Any]
    spells: list[Any]
    updated_at: datetime

    model_config = {"from_attributes": True}


class UpdateSheetRequest(BaseModel):
    stats: dict[str, Any] | None = None
    inventory: list[Any] | None = None
    skills: dict[str, Any] | None = None
    spells: list[Any] | None = None
    display_name: str | None = None
    description: str | None = None
    is_alive: bool | None = None


class CreateSheetRequest(BaseModel):
    character_id: uuid.UUID | None = None
    is_player: bool = True
    display_name: str | None = None
    description: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    inventory: list[Any] = Field(default_factory=list)
    skills: dict[str, Any] = Field(default_factory=dict)
    spells: list[Any] = Field(default_factory=list)


# ── Dice Rolls ────────────────────────────────────────────────────────────────


class RollRequest(BaseModel):
    expression: str = Field(min_length=1, max_length=50)
    context: str | None = None
    dc: int | None = None
    stat_used: str | None = None
    advantage: bool = False
    disadvantage: bool = False


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
    dc: int | None
    success: bool | None
    advantage: bool
    disadvantage: bool
    stat_used: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RollListResponse(BaseModel):
    rolls: list[RollResponse]
    total: int


class RollStatsResponse(BaseModel):
    total_rolls: int
    nat20_count: int
    nat1_count: int
    avg_roll: float | None
    highest: int | None
    lowest: int | None


# ── Game Events ───────────────────────────────────────────────────────────────


class GameEventResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    message_id: uuid.UUID | None
    player_action: str
    ruling: str
    ruling_reason: str | None
    approved_action: str | None
    forbidden_elements: list[str]
    stat_changes: list[Any]
    inventory_changes: list[Any]
    skill_changes: list[Any]
    dm_model_used: str | None
    dm_prompt_tokens: int | None
    dm_completion_tokens: int | None
    dm_latency_ms: int | None
    pre_validation: dict[str, Any] | None
    question_text: str | None
    player_answer: str | None
    summary: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class GameEventListResponse(BaseModel):
    events: list[GameEventResponse]
    total: int


class AnswerQuestionRequest(BaseModel):
    event_id: uuid.UUID
    answer: str = Field(min_length=1, max_length=2000)


# ── DM Config Attachments ─────────────────────────────────────────────────────


class CreateAttachmentRequest(BaseModel):
    ruleset_id: uuid.UUID
    character_id: uuid.UUID | None = None
    chat_id: uuid.UUID | None = None
    mode: str = Field(default="required", pattern=r"^(required|optional)$")


class AttachmentResponse(BaseModel):
    id: uuid.UUID
    ruleset_id: uuid.UUID
    character_id: uuid.UUID | None
    chat_id: uuid.UUID | None
    mode: str
    created_at: datetime

    model_config = {"from_attributes": True}


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
    narrative_context: str | None = None  # full [DUNGEON MIND RULING] block for narrative AI
    rejection_reason: str | None = None
    ask_question: str | None = None
    stat_changes: list[StatChangeSummary] = Field(default_factory=list)
    roll_ids: list[uuid.UUID] = Field(default_factory=list)
    death_state: bool = False
    game_event_id: uuid.UUID | None = None
