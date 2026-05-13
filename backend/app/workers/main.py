"""ARQ worker entry point.

Run with: ``arq app.workers.main.WorkerSettings``

Registered tasks:

* ``generate_image(job_id, job_kind)`` — Tier 2 / Tier 3 / HQ job runner.
  Dispatches to ``run_job`` or ``run_composite_job`` per ``job_kind``.
* ``run_backdrop_worker(regenerate=False)`` — pre-generates the backdrop
  library from ``backdrop_specs.yaml``.
* ``run_pose_worker(character_id, lora_version)`` — pre-generates the pose
  library for a character LoRA.

The application currently uses ``asyncio.create_task`` in
``app.modules.images.service.create_and_queue_job``. When Redis is provisioned,
swap that for ``await ctx_pool.enqueue_job("generate_image", job_id, kind)``.
"""

from __future__ import annotations

import uuid

from arq.connections import RedisSettings

from app.core.config import settings
from app.modules.images.models import ImageJobKind
from app.modules.images.service import run_composite_job, run_job
from app.workers.backdrop_generator import generate_backdrops as _backdrops_task
from app.workers.character_pose_generator import generate_character_poses as _poses_task


async def generate_image(ctx: dict, job_id: str, job_kind: str = "single") -> None:
    """ARQ task wrapper. ``job_id`` arrives as a string after JSON encode."""
    jid = uuid.UUID(job_id)
    if job_kind == ImageJobKind.composite.value:
        await run_composite_job(jid)
    else:
        # ``single`` and ``hq`` share run_job today; HQ stub logs a warning.
        await run_job(jid)


async def run_backdrop_worker(ctx: dict, *, regenerate: bool = False) -> dict:
    return await _backdrops_task(ctx, regenerate=regenerate)


async def run_pose_worker(
    ctx: dict, *, character_id: str, lora_version: str, limit: int | None = None
) -> dict:
    return await _poses_task(
        ctx,
        character_id=uuid.UUID(character_id),
        lora_version=lora_version,
        limit=limit,
    )


class WorkerSettings:
    functions = [generate_image, run_backdrop_worker, run_pose_worker]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
