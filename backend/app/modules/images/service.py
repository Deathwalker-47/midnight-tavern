"""Image generation orchestration.

Flow per ``run_job``:

1. Mark job ``running`` and publish ``image_started``.
2. Build the final prompt + match LoRAs (``prompt_builder.build_image_prompt``).
3. Run ``ProviderChain.generate`` over configured providers in order.
4. Persist bytes to storage; insert ``ImageOutput`` row.
5. Mark job ``completed`` and publish ``image_completed`` with the output URL.
6. On any failure, mark ``failed`` and publish ``image_failed``.

Events are also stored in an in-process pubsub so the SSE endpoint can stream
without Redis when running tests/local. Once Redis is the worker substrate
this becomes pub/sub-backed.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.modules.images.cache import get_image_cache, make_cache_key
from app.modules.images.lora import PROVIDER_MAX_LORAS, LoRAManager
from app.modules.images.models import ImageJob, ImageJobStatus, ImageOutput
from app.modules.images.prompt_builder import build_image_prompt
from app.modules.images.providers import (
    DummyProvider,
    FALProvider,
    ProviderChain,
    RunwareProvider,
    WavespeedProvider,
)
from app.modules.images.providers.chain import AllProvidersFailedError
from app.modules.images.storage import get_storage

logger = structlog.get_logger(__name__)


# ── In-process pub/sub ───────────────────────────────────────────────────────


class _JobChannel:
    """Per-job queue of SSE-shaped events. Replace with Redis pub/sub later."""

    def __init__(self) -> None:
        self.queues: dict[uuid.UUID, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        # Replay buffer: recent events so a slow client can still catch up.
        self.history: dict[uuid.UUID, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=20)
        )

    async def publish(self, job_id: uuid.UUID, event: dict[str, Any]) -> None:
        self.history[job_id].append(event)
        for q in self.queues[job_id]:
            await q.put(event)

    def subscribe(self, job_id: uuid.UUID) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # Replay history first.
        for ev in self.history[job_id]:
            q.put_nowait(ev)
        self.queues[job_id].append(q)
        return q

    def unsubscribe(self, job_id: uuid.UUID, q: asyncio.Queue) -> None:
        try:
            self.queues[job_id].remove(q)
        except ValueError:
            pass

    def cleanup(self, job_id: uuid.UUID) -> None:
        self.queues.pop(job_id, None)
        self.history.pop(job_id, None)


channel = _JobChannel()


# ── Provider construction ───────────────────────────────────────────────────

_PROVIDER_FACTORY: dict[str, Any] = {
    "dummy": DummyProvider,
    "runware": RunwareProvider,
    "wavespeed": WavespeedProvider,
    "fal": FALProvider,
}


def _build_chain() -> ProviderChain:
    names = settings.image_provider_order_list or ["dummy"]
    providers = []
    for name in names:
        factory = _PROVIDER_FACTORY.get(name)
        if factory is None:
            logger.warning("unknown_image_provider", provider=name)
            continue
        providers.append(factory())
    if not providers:
        providers.append(DummyProvider())
    return ProviderChain(providers)


# ── Job lifecycle ────────────────────────────────────────────────────────────


async def run_job(job_id: uuid.UUID) -> None:
    """Execute a queued image job. Runs in a background task."""
    async with AsyncSessionLocal() as db:
        job = await _load_job(db, job_id)
        if job is None:
            logger.warning("image_job_missing", job_id=str(job_id))
            return

        try:
            job.status = ImageJobStatus.running
            await db.commit()
            await channel.publish(
                job_id, {"event": "image_started", "data": {"job_id": str(job_id)}}
            )

            final_prompt, final_negative, matched_loras = await build_image_prompt(
                db,
                user_prompt=job.prompt_user,
                negative_prompt=job.negative_prompt,
                chat_id=job.chat_id,
                character_id=job.character_id,
            )
            job.prompt_final = final_prompt
            job.negative_prompt = final_negative
            await db.commit()

            chain = _build_chain()
            # Per-provider LoRA cap is applied per provider in the chain.
            # For simplicity here we send the role-capped list as-is; the
            # provider modules truncate to their own cap.
            provider_loras = LoRAManager.build_lora_list(
                matched_loras, max_loras=PROVIDER_MAX_LORAS.get("runware", 12)
            )
            params: dict[str, Any] = {
                "steps": job.steps,
                "cfg_scale": job.cfg_scale,
                "width": job.width,
                "height": job.height,
                "seed": job.seed if job.seed is not None else -1,
            }

            # Request-level cache lookup (deterministic seed only).
            cache = get_image_cache()
            cache_key = make_cache_key(final_prompt, final_negative, provider_loras, params)
            cached_url: str | None = None
            if cache_key is not None:
                cached_url = await cache.get(cache_key)

            if cached_url is not None:
                # Cache hit: skip provider call, reuse the prior storage URL.
                output = ImageOutput(
                    job_id=job_id,
                    storage_url=cached_url,
                    mime_type="image/png",
                    width=job.width,
                    height=job.height,
                )
                db.add(output)
                job.status = ImageJobStatus.completed
                job.provider_used = "cache"
                job.providers_attempted = ["cache"]
                job.loras_used = provider_loras
                job.completed_at = datetime.now(timezone.utc)
                await db.commit()
                await channel.publish(
                    job_id,
                    {
                        "event": "image_completed",
                        "data": {
                            "job_id": str(job_id),
                            "provider": "cache",
                            "cache_hit": True,
                            "outputs": [{"storage_url": cached_url}],
                        },
                    },
                )
                return

            await channel.publish(
                job_id,
                {
                    "event": "image_progress",
                    "data": {"job_id": str(job_id), "stage": "generating"},
                },
            )
            result = await chain.generate(final_prompt, final_negative, provider_loras, params)

            storage = get_storage()
            url = await storage.save(job_id, idx=0, data=result.image_bytes, ext="png")

            output = ImageOutput(
                job_id=job_id,
                storage_url=url,
                mime_type="image/png",
                bytes_size=len(result.image_bytes),
                width=job.width,
                height=job.height,
            )
            db.add(output)
            job.status = ImageJobStatus.completed
            job.provider_used = result.provider_used
            job.providers_attempted = result.providers_attempted
            job.loras_used = provider_loras
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()

            if cache_key is not None:
                try:
                    await cache.set(cache_key, url)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("image_cache_set_failed", error=str(exc))

            await channel.publish(
                job_id,
                {
                    "event": "image_completed",
                    "data": {
                        "job_id": str(job_id),
                        "provider": result.provider_used,
                        "cache_hit": False,
                        "outputs": [{"storage_url": url}],
                    },
                },
            )
        except AllProvidersFailedError as exc:
            job.status = ImageJobStatus.failed
            job.error_code = "all_providers_failed"
            job.error_message = exc.last_error
            job.providers_attempted = exc.attempted
            await db.commit()
            await channel.publish(
                job_id,
                {
                    "event": "image_failed",
                    "data": {"job_id": str(job_id), "error": exc.last_error},
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("image_job_error", job_id=str(job_id))
            job.status = ImageJobStatus.failed
            job.error_code = "internal_error"
            job.error_message = str(exc)
            await db.commit()
            await channel.publish(
                job_id,
                {
                    "event": "image_failed",
                    "data": {"job_id": str(job_id), "error": str(exc)},
                },
            )


async def run_hq_job(job_id: uuid.UUID) -> None:
    """Phase 5 stub for the high-quality (sequential inpainting) path.

    The plan §6 calls for ``MultiCharPipeline`` to live behind this endpoint —
    that pipeline isn't ported into midnight-tavern yet (it lives in the
    upstream ``flux_lora_bridge.py``). Until it is, the HQ path falls back to
    the standard single-character generator, which still produces a valid
    image but without the quality bump. ``job_kind="hq"`` is recorded so a
    future port can swap this stub for the real pipeline with no API change.
    """
    logger.warning("hq_pipeline_not_implemented_falling_back_to_single", job_id=str(job_id))
    await run_job(job_id)


async def create_and_queue_hq_job(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    prompt: str,
    negative_prompt: str | None,
    chat_id: uuid.UUID | None,
    character_id: uuid.UUID | None,
    width: int,
    height: int,
    steps: int,
    cfg_scale: float,
    seed: int | None,
) -> ImageJob:
    """Create an async high-quality image job. Documented latency 30-60s."""
    from app.modules.images.models import ImageJobKind

    job = ImageJob(
        user_id=user_id,
        chat_id=chat_id,
        character_id=character_id,
        prompt_user=prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        steps=steps,
        cfg_scale=int(cfg_scale),
        seed=seed,
        status=ImageJobStatus.queued,
        job_kind=ImageJobKind.hq,
    )
    db.add(job)
    await db.commit()
    job = await _load_job(db, job.id) or job
    await channel.publish(
        job.id,
        {"event": "image_queued", "data": {"job_id": str(job.id), "kind": "hq"}},
    )
    asyncio.create_task(run_hq_job(job.id))
    return job


async def run_composite_job(job_id: uuid.UUID) -> None:
    """Run a Tier 3 composite job. Implemented in Phase 3 (composer.py).

    Stub here so the worker entry point can import this symbol at module
    load and the API can dispatch composite jobs through the same lifecycle
    as single-character jobs. The real implementation lands alongside
    ``composer.py`` and ``scene_classifier.py``.
    """
    from app.modules.images.composite_service import run_composite_job as _impl

    await _impl(job_id)


async def _load_job(db: AsyncSession, job_id: uuid.UUID) -> ImageJob | None:
    result = await db.execute(
        select(ImageJob)
        .options(selectinload(ImageJob.outputs))
        .where(ImageJob.id == job_id, ImageJob.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def create_and_queue_job(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    prompt: str,
    negative_prompt: str | None,
    chat_id: uuid.UUID | None,
    character_id: uuid.UUID | None,
    width: int,
    height: int,
    steps: int,
    cfg_scale: float,
    seed: int | None,
) -> ImageJob:
    job = ImageJob(
        user_id=user_id,
        chat_id=chat_id,
        character_id=character_id,
        prompt_user=prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        steps=steps,
        cfg_scale=int(cfg_scale),
        seed=seed,
        status=ImageJobStatus.queued,
    )
    db.add(job)
    await db.commit()
    # Re-fetch with outputs eager-loaded so the response serializer doesn't
    # trip on a lazy-load.
    job = await _load_job(db, job.id) or job
    await channel.publish(
        job.id, {"event": "image_queued", "data": {"job_id": str(job.id)}}
    )
    # Fire-and-forget background task. ARQ replacement would call enqueue here.
    asyncio.create_task(run_job(job.id))
    return job


async def create_and_queue_composite_job(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    scene_description: str,
    character_ids: list[uuid.UUID],
    chat_id: uuid.UUID | None,
    width: int,
    height: int,
    seed: int | None,
) -> ImageJob:
    """Create a Tier 3 composite job and kick off background processing."""
    from app.modules.images.models import ImageJobKind

    job = ImageJob(
        user_id=user_id,
        chat_id=chat_id,
        prompt_user=scene_description,
        width=width,
        height=height,
        steps=settings.COMPOSITE_LIGHTING_STEPS,
        cfg_scale=4,
        seed=seed,
        status=ImageJobStatus.queued,
        job_kind=ImageJobKind.composite,
        composite_meta={
            "scene_description": scene_description,
            "character_ids": [str(cid) for cid in character_ids],
        },
    )
    db.add(job)
    await db.commit()
    job = await _load_job(db, job.id) or job
    await channel.publish(
        job.id,
        {"event": "image_queued", "data": {"job_id": str(job.id), "kind": "composite"}},
    )
    asyncio.create_task(run_composite_job(job.id))
    return job


async def get_job(
    db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID
) -> ImageJob | None:
    result = await db.execute(
        select(ImageJob)
        .options(selectinload(ImageJob.outputs))
        .where(
            ImageJob.id == job_id,
            ImageJob.user_id == user_id,
            ImageJob.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def cancel_job(db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID) -> ImageJob | None:
    job = await get_job(db, job_id, user_id)
    if job is None:
        return None
    if job.status in (ImageJobStatus.completed, ImageJobStatus.failed, ImageJobStatus.canceled):
        return job
    job.status = ImageJobStatus.canceled
    await db.commit()
    await channel.publish(
        job_id, {"event": "image_canceled", "data": {"job_id": str(job_id)}}
    )
    return job


# ── SSE stream ───────────────────────────────────────────────────────────────


async def stream_job_events(job_id: uuid.UUID) -> AsyncGenerator[str, None]:
    """Yield SSE-formatted strings for a job's lifecycle.

    Closes after a terminal event (``image_completed``/``image_failed``/
    ``image_canceled``).
    """
    queue = channel.subscribe(job_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=300)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            event_name = event["event"]
            event_data = json.dumps(event["data"])
            yield f"event: {event_name}\ndata: {event_data}\n\n"
            if event_name in ("image_completed", "image_failed", "image_canceled"):
                break
    finally:
        channel.unsubscribe(job_id, queue)
