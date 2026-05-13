"""Image generation Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.images.models import ImageJobKind, ImageJobStatus


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


class CompositeRequest(BaseModel):
    """Tier 3 composite scene request.

    The classifier picks the backdrop + per-character pose given
    ``scene_description`` and the character set. Caller does not have to
    specify a backdrop directly — let the system choose.
    """

    chat_id: uuid.UUID | None = None
    scene_description: str = Field(..., min_length=1, max_length=2000)
    character_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=5)
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    seed: int | None = None


class BackdropResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    description: str
    tags: list[str]
    width: int
    height: int
    storage_url: str
    lighting_profile: dict[str, Any]
    aspect_ratio: str
    generated_at: datetime


class CharacterPoseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    character_id: uuid.UUID
    lora_version: str
    pose_slug: str
    expression_slug: str
    framing: str
    facing: str
    transparent_storage_url: str
    width: int
    height: int
    bounding_box: dict[str, Any]
    lighting_profile: dict[str, Any]
    generated_at: datetime


class SceneCompositeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scene_hash: str
    backdrop_id: uuid.UUID
    pose_ids: list[uuid.UUID]
    layout_template: str
    storage_url: str
    generated_at: datetime
    last_used_at: datetime


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
    job_kind: ImageJobKind = ImageJobKind.single
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
    composite_meta: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    outputs: list[ImageOutputResponse] = Field(default_factory=list)
