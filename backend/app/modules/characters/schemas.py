"""Character request/response schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateCharacterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    personality: str | None = None
    system_prompt: str | None = None
    avatar_url: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(default_factory=list)
    is_public: bool = False


class UpdateCharacterRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    personality: str | None = None
    system_prompt: str | None = None
    avatar_url: str | None = None
    tags: list[str] | None = None
    is_public: bool | None = None


class CharacterResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    description: str | None
    personality: str | None
    system_prompt: str | None
    avatar_url: str | None
    tags: list[Any]
    is_public: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
