"""Image generation HTTP API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.auth.service import get_current_user
from app.modules.chats.service import get_chat
from app.modules.images.schemas import GenerateImageRequest, ImageJobResponse
from app.modules.images.service import (
    cancel_job,
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
