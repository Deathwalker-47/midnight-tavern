"""Image generation HTTP API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.auth.service import get_current_user
from app.modules.characters.service import get_character
from app.modules.chats.service import get_chat
from app.modules.images.models import Backdrop, CharacterPose
from app.modules.images.schemas import (
    BackdropResponse,
    CharacterPoseResponse,
    CompositeRequest,
    GenerateImageRequest,
    ImageJobResponse,
)
from app.modules.images.service import (
    cancel_job,
    create_and_queue_composite_job,
    create_and_queue_hq_job,
    create_and_queue_job,
    get_job,
    stream_job_events,
)

router = APIRouter(prefix="/images", tags=["images"])


@router.post(
    "/generate", response_model=ImageJobResponse, status_code=status.HTTP_201_CREATED
)
async def generate(
    body: GenerateImageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImageJobResponse:
    if body.chat_id is not None:
        await get_chat(db, body.chat_id, current_user.id)
    job = await create_and_queue_job(
        db,
        user_id=current_user.id,
        prompt=body.prompt,
        negative_prompt=body.negative_prompt,
        chat_id=body.chat_id,
        character_id=body.character_id,
        width=body.width,
        height=body.height,
        steps=body.steps,
        cfg_scale=body.cfg_scale,
        seed=body.seed,
    )
    return ImageJobResponse.model_validate(job)


@router.get("/jobs/{job_id}", response_model=ImageJobResponse)
async def job_detail(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImageJobResponse:
    job = await get_job(db, job_id, current_user.id)
    if job is None:
        raise AppError("Job not found", status_code=404, error_code="job_not_found")
    return ImageJobResponse.model_validate(job)


@router.post("/jobs/{job_id}/cancel", response_model=ImageJobResponse)
async def cancel(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImageJobResponse:
    job = await cancel_job(db, job_id, current_user.id)
    if job is None:
        raise AppError("Job not found", status_code=404, error_code="job_not_found")
    return ImageJobResponse.model_validate(job)


@router.get("/jobs/{job_id}/events")
async def events(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    job = await get_job(db, job_id, current_user.id)
    if job is None:
        raise AppError("Job not found", status_code=404, error_code="job_not_found")

    return StreamingResponse(
        stream_job_events(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Tier 3 composite endpoint (Phase 3) ────────────────────────────────────


@router.post(
    "/generate_hq",
    response_model=ImageJobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_hq(
    body: GenerateImageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImageJobResponse:
    """Async high-quality image generation.

    Documented latency: 30-60s once ``MultiCharPipeline`` is ported. Today
    this falls through to the single-character path with ``job_kind="hq"``
    recorded so consumers can track usage. Poll the standard job/events
    endpoints — the API surface is identical to ``/generate``.
    """
    if body.chat_id is not None:
        await get_chat(db, body.chat_id, current_user.id)
    job = await create_and_queue_hq_job(
        db,
        user_id=current_user.id,
        prompt=body.prompt,
        negative_prompt=body.negative_prompt,
        chat_id=body.chat_id,
        character_id=body.character_id,
        width=body.width,
        height=body.height,
        steps=body.steps,
        cfg_scale=body.cfg_scale,
        seed=body.seed,
    )
    return ImageJobResponse.model_validate(job)


@router.post(
    "/composite",
    response_model=ImageJobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def composite(
    body: CompositeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImageJobResponse:
    """Tier 3 cached-composite generation.

    Picks a backdrop + per-character pose from the asset library, composites
    them in PIL, runs a low-strength img2img lighting unification pass, and
    caches the result keyed by ``scene_hash``.

    Expected p95 latency: <13s cache miss, <1s cache hit.
    """
    if body.chat_id is not None:
        await get_chat(db, body.chat_id, current_user.id)
    if len(body.character_ids) < 1 or len(body.character_ids) > 5:
        raise AppError(
            "composite supports 1-5 characters",
            status_code=400,
            error_code="invalid_character_count",
        )
    # Object-level authz: each character must be owned by the caller or
    # marked public. ``get_character`` raises 404 otherwise.
    seen: set[uuid.UUID] = set()
    for cid in body.character_ids:
        if cid in seen:
            raise AppError(
                "duplicate character_id in composite request",
                status_code=400,
                error_code="duplicate_character_id",
            )
        seen.add(cid)
        await get_character(db, cid, current_user.id)

    from app.core.config import settings as _settings

    job = await create_and_queue_composite_job(
        db,
        user_id=current_user.id,
        scene_description=body.scene_description,
        character_ids=body.character_ids,
        chat_id=body.chat_id,
        width=body.width or _settings.COMPOSITE_DEFAULT_WIDTH,
        height=body.height or _settings.COMPOSITE_DEFAULT_HEIGHT,
        seed=body.seed,
    )
    return ImageJobResponse.model_validate(job)


# ── Tier 3 asset library (Phase 2) ─────────────────────────────────────────


@router.get("/backdrops", response_model=list[BackdropResponse])
async def list_backdrops(
    tag: list[str] = Query(default_factory=list),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[BackdropResponse]:
    """List backdrops, optionally filtered by tags (overlap, any-of).

    The tag set is shared across users — backdrops are not user-scoped.
    Authentication is still required to keep the endpoint behind login.
    """
    result = await db.execute(select(Backdrop).order_by(Backdrop.slug))
    rows = list(result.scalars().all())
    if tag:
        wanted = {t.lower() for t in tag}
        rows = [b for b in rows if wanted.intersection({t.lower() for t in (b.tags or [])})]
    return [BackdropResponse.model_validate(b) for b in rows]


@router.get(
    "/characters/{character_id}/poses",
    response_model=list[CharacterPoseResponse],
)
async def list_character_poses(
    character_id: uuid.UUID,
    pose_slug: str | None = Query(default=None),
    expression_slug: str | None = Query(default=None),
    facing: str | None = Query(default=None),
    lora_version: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CharacterPoseResponse]:
    query = select(CharacterPose).where(CharacterPose.character_id == character_id)
    if pose_slug:
        query = query.where(CharacterPose.pose_slug == pose_slug)
    if expression_slug:
        query = query.where(CharacterPose.expression_slug == expression_slug)
    if facing:
        query = query.where(CharacterPose.facing == facing)
    if lora_version:
        query = query.where(CharacterPose.lora_version == lora_version)
    result = await db.execute(query.order_by(CharacterPose.pose_slug))
    return [CharacterPoseResponse.model_validate(p) for p in result.scalars().all()]
