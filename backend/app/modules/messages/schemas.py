"""Messages schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.modules.messages.models import MessageRole


class MessageResponse(BaseModel):
    id: uuid.UUID
    chat_id: uuid.UUID
    role: MessageRole
    content: str
    metadata_: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageListResponse(BaseModel):
    messages: list[MessageResponse]
    total: int
    has_more: bool


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=16000)
