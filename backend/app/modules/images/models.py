"""Image generation database models.

Three-tier scope (Sprint B, see ``docs/plans/PLAN_image_generation_architecture.md``):

* ``ImageJob`` / ``ImageOutput`` — Tier 2 single-character path (existing).
* ``Backdrop`` — Tier 3 cached scene backgrounds (new).
* ``CharacterPose`` — per-character library of transparent poses (new).
* ``SceneComposite`` — final composite cache keyed by scene hash (new).

``ImageJob.job_kind`` distinguishes single-character generation, Tier 3 composite
runs, and the async HQ path.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin


class ImageJobStatus(str, PyEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


class ImageJobKind(str, PyEnum):
    single = "single"      # Tier 2 single-character generation
    composite = "composite"  # Tier 3 cached scene composite
    hq = "hq"              # Async high-quality (MultiCharPipeline placeholder)


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
    job_kind: Mapped[ImageJobKind] = mapped_column(
        Enum(ImageJobKind, name="image_job_kind"),
        nullable=False,
        default=ImageJobKind.single,
        server_default="single",
    )
    composite_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
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


# ── Tier 3 asset library ─────────────────────────────────────────────────────


class Backdrop(Base):
    """Cached scene backdrop (no characters). Generated once, reused per scene.

    Backdrops are tag-indexed (``tags`` is a Postgres TEXT[]). Tier 3 selects
    backdrops by tag overlap against the scene classifier output.
    """

    __tablename__ = "backdrops"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # JSONB list of tag strings (e.g. ["indoor", "tavern", "warm-light"]).
    # Indexed via a GIN expression index in the migration for tag-overlap lookups.
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=1536)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    storage_url: Mapped[str] = mapped_column(String(500), nullable=False)
    lighting_profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    aspect_ratio: Mapped[str] = mapped_column(String(20), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    generation_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class CharacterPose(Base):
    """Per-character pose library.

    Background-removed (transparent PNG) so it composites over any backdrop.
    ``lora_version`` enables cache invalidation when a user retrains a
    character LoRA — old rows stay until cleanup, new rows live alongside.
    """

    __tablename__ = "character_poses"
    __table_args__ = (
        UniqueConstraint(
            "character_id",
            "lora_version",
            "pose_slug",
            "expression_slug",
            "facing",
            name="uq_character_poses_lookup",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    character_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lora_version: Mapped[str] = mapped_column(String(60), nullable=False)
    pose_slug: Mapped[str] = mapped_column(String(80), nullable=False)
    expression_slug: Mapped[str] = mapped_column(String(80), nullable=False)
    framing: Mapped[str] = mapped_column(String(40), nullable=False)
    facing: Mapped[str] = mapped_column(String(40), nullable=False)
    transparent_storage_url: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_storage_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    bounding_box: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    lighting_profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    generation_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class SceneComposite(Base):
    """Final composite cache keyed by (backdrop, sorted poses, layout) hash.

    Hash format: SHA-256 hex of ``f"{backdrop_id}|{sorted(pose_ids)}|{layout}"``.
    Hit short-circuits the full Tier 3 pipeline.
    """

    __tablename__ = "scene_composites"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scene_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    backdrop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("backdrops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    pose_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    layout_template: Mapped[str] = mapped_column(String(40), nullable=False)
    storage_url: Mapped[str] = mapped_column(String(500), nullable=False)
    composite_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
