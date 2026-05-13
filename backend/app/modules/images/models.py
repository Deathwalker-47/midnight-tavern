"""Image generation database models."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin


class ImageJobStatus(str, PyEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


class ImageJob(Base, SoftDeleteMixin):
    """One image generation request."""

    __tablename__ = "image_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    character_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    status: Mapped[ImageJobStatus] = mapped_column(
        Enum(ImageJobStatus, name="image_job_status"),
        nullable=False,
        default=ImageJobStatus.queued,
        index=True,
    )
    prompt_user: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_final: Mapped[str | None] = mapped_column(Text, nullable=True)
    negative_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    steps: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    cfg_scale: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loras_used: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    provider_used: Mapped[str | None] = mapped_column(String(50), nullable=True)
    providers_attempted: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    outputs: Mapped[list["ImageOutput"]] = relationship(
        "ImageOutput", back_populates="job", cascade="all, delete-orphan"
    )


class ImageOutput(Base):
    """One generated image bound to a job."""

    __tablename__ = "image_outputs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("image_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    storage_url: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(50), nullable=False, default="image/png")
    bytes_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped["ImageJob"] = relationship("ImageJob", back_populates="outputs")
