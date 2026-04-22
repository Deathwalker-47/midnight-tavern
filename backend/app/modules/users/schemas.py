"""Users module schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ProviderKeyRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    api_key: str = Field(min_length=1)


class ProviderKeyResponse(BaseModel):
    id: uuid.UUID
    provider: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
