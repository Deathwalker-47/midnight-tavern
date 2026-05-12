"""Chats schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CreateChatRequest(BaseModel):
    character_id: uuid.UUID
    title: str | None = Field(default=None, max_length=200)


class UpdateChatRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ChatResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    character_id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
