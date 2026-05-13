"""ARQ worker entry point.

Run with: ``arq app.workers.main.WorkerSettings``

This is currently a scaffold. The default code path enqueues image jobs as
in-process ``asyncio.create_task`` calls (see
``app.modules.images.service.create_and_queue_job``). To migrate to ARQ:

1. Provision Redis at ``settings.REDIS_URL``.
2. Replace ``asyncio.create_task(run_job(job.id))`` in
   ``create_and_queue_job`` with ``await ctx_pool.enqueue_job("generate_image", job_id)``.
3. Inject ``ctx_pool`` (``arq.create_pool``) into the request scope so the
   router can dispatch.
"""

from __future__ import annotations

import uuid

from arq.connections import RedisSettings

from app.core.config import settings
from app.modules.images.service import run_job


async def generate_image(ctx: dict, job_id: str) -> None:
    """ARQ task wrapper. ``job_id`` arrives as a string after JSON encode."""
    await run_job(uuid.UUID(job_id))


class WorkerSettings:
    functions = [generate_image]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
