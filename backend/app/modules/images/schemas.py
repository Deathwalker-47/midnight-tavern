"""Image generation Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.images.models import ImageJobStatus


class GenerateImageRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    negative_prompt: str | None = None
    chat_id: uuid.UUID | None = None
    character_id: uuid.UUID | None = None
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    steps: int = Field(default=40, ge=1, le=100)
    cfg_scale: float = Field(default=4.0, ge=1.0, le=20.0)
    seed: int | None = None


class ImageOutputResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    storage_url: str
    mime_type: str
    bytes_size: int | None = None
    width: int | None = None
    height: int | None = None
    created_at: datetime


class ImageJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    chat_id: uuid.UUID | None = None
    character_id: uuid.UUID | None = None
    status: ImageJobStatus
    prompt_user: str
    prompt_final: str | None = None
    negative_prompt: str | None = None
    width: int
    height: int
    steps: int
    cfg_scale: int
    seed: int | None = None
    loras_used: list[dict] = Field(default_factory=list)
    provider_used: str | None = None
    providers_attempted: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    outputs: list[ImageOutputResponse] = Field(default_factory=list)
