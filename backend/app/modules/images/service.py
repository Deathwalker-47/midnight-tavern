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
    """Phase 5 HQ MultiCharPipeline — sequential inpainting.

    Ported from Silly-Tavern-Flux-Bridge/flux_lora_bridge.py ``MultiCharPipeline``
    (~lines 1900-2250). Flow:

    1. Pass 0 — backdrop generation with no character LoRAs.
    2. Pass 1..N — inpaint each character into its layout slot using
       ``provider.generate_inpaint`` over a feathered mask.
    3. Pass N+1 — optional whole-image low-strength img2img over the seam
       mask to harmonize lighting between adjacent character regions.
       Runs when ``HQ_HARMONIZE_ENABLED`` is true and N >= 3.

    Slot rectangles come from ``composer.LAYOUT_TEMPLATES`` — same geometry
    the Tier 3 composite path uses. ``MaskGenerator`` builds white-on-black
    feathered PNG masks from the pixel rectangles.

    Falls back to ``run_job`` (single-shot Tier 2) when no character_ids can
    be resolved — keeps the contract that ``/generate_hq`` always produces
    *something* even for free-form prompts.
    """
    from app.modules.characters.models import Character
    from app.modules.images.composer import pick_layout
    from app.modules.images.mask_generator import (
        generate_seam_mask,
        generate_slot_mask,
    )

    async with AsyncSessionLocal() as db:
        job = await _load_job(db, job_id)
        if job is None:
            logger.warning("hq_job_missing", job_id=str(job_id))
            return

        # Resolve participating character_ids. Order: composite_meta override
        # (future multi-char endpoint), then the single character_id field.
        meta: dict[str, Any] = job.composite_meta or {}
        character_ids: list[uuid.UUID] = []
        for cid_str in meta.get("character_ids") or []:
            try:
                character_ids.append(uuid.UUID(cid_str))
            except (ValueError, TypeError):
                pass
        if not character_ids and job.character_id is not None:
            character_ids = [job.character_id]

        if not character_ids:
            logger.info("hq_no_characters_fallback_to_single", job_id=str(job_id))
            await run_job(job_id)
            return

        try:
            job.status = ImageJobStatus.running
            await db.commit()
            await channel.publish(
                job_id,
                {"event": "image_started", "data": {"job_id": str(job_id), "kind": "hq"}},
            )

            # Honor the requested dimensions from the job row; the request
            # schema defaults to 1024×1024 but callers can pass arbitrary
            # values up to 2048. The composer scales its 1536×1024 reference
            # slots to whatever canvas is in play.
            canvas_w = job.width
            canvas_h = job.height
            num_chars = min(len(character_ids), 5)
            character_ids = character_ids[:num_chars]
            layout_name, slots = pick_layout(num_chars)

            # Scale the reference 1536x1024 layout slots to the HQ canvas.
            ref_w, ref_h = 1536, 1024
            sx, sy = canvas_w / ref_w, canvas_h / ref_h
            scaled_slots: list[tuple[int, int, int, int]] = [
                (int(x * sx), int(y * sy), int(w * sx), int(h * sy))
                for (x, y, w, h) in slots
            ]

            characters: list[Character] = []
            for cid in character_ids:
                result = await db.execute(
                    select(Character).where(
                        Character.id == cid, Character.deleted_at.is_(None)
                    )
                )
                ch = result.scalar_one_or_none()
                if ch is None:
                    raise ValueError(f"character {cid} not found")
                characters.append(ch)

            # Pass 0 — backdrop. No character LoRAs; use job.prompt_user as the
            # scene description with a "wide shot, group composition" hint so
            # the model leaves room for characters.
            await channel.publish(
                job_id,
                {
                    "event": "image_progress",
                    "data": {"job_id": str(job_id), "stage": "backdrop"},
                },
            )
            bg_prompt = (
                f"{job.prompt_user.strip()}, wide shot, group composition, "
                f"open space for {num_chars} character(s), "
                "photorealistic, detailed environment, cinematic lighting"
            )
            bg_negative = "low quality, blurry, deformed"
            bg_params: dict[str, Any] = {
                "steps": settings.HQ_BG_STEPS,
                "cfg_scale": job.cfg_scale,
                "width": canvas_w,
                "height": canvas_h,
                "seed": job.seed if job.seed is not None else -1,
            }
            chain = _build_chain()
            per_pass_timeout = int(
                settings.IMAGE_PROVIDER_TIMEOUT_S * settings.HQ_PER_PASS_TIMEOUT_MULTIPLIER
            )
            chain.timeout_s = per_pass_timeout
            bg_result = await chain.generate(bg_prompt, bg_negative, [], bg_params)
            current_image = bg_result.image_bytes
            providers_attempted: list[str] = list(bg_result.providers_attempted)

            # Strength schedule: front characters need higher strength to fully
            # replace the backdrop region; background characters can use less.
            # Our composer slots aren't z-stratified (z-order = list order), so
            # we use foreground strength for all by default. Background slots
            # for layouts 3-5 (the smaller, upper ones) get midground strength.
            strength_by_idx: dict[int, float] = {}
            for idx in range(num_chars):
                # Layouts 3,4,5 have the "back" slots later in the list with
                # smaller y values; we approximate by area: smaller slot = bg.
                _, _, w, h = scaled_slots[idx]
                strength_by_idx[idx] = (
                    settings.HQ_FG_STRENGTH if w * h > (canvas_w * canvas_h * 0.25)
                    else settings.HQ_MG_STRENGTH
                )

            # Pass 1..N — per-character inpaint.
            successful_inpaints = 0
            failed_inpaints = 0
            inpaint_failure_reason: str | None = None
            for idx, character in enumerate(characters):
                slot = scaled_slots[idx]
                await channel.publish(
                    job_id,
                    {
                        "event": "image_progress",
                        "data": {
                            "job_id": str(job_id),
                            "stage": f"character_{idx + 1}",
                            "character_id": str(character.id),
                        },
                    },
                )

                # Build per-character prompt + LoRAs.
                ch_prompt, ch_negative, matched_loras = await build_image_prompt(
                    db,
                    user_prompt=job.prompt_user,
                    negative_prompt=job.negative_prompt,
                    chat_id=job.chat_id,
                    character_id=character.id,
                )
                provider_loras = LoRAManager.build_lora_list(
                    matched_loras, max_loras=PROVIDER_MAX_LORAS.get("runware", 12)
                )

                mask_bytes = generate_slot_mask(
                    canvas_w, canvas_h, slot, feather_px=settings.HQ_FEATHER_PX
                )

                inpaint_params: dict[str, Any] = {
                    "cfg_scale": job.cfg_scale,
                    "width": canvas_w,
                    "height": canvas_h,
                }
                try:
                    next_image, used = await _run_inpaint_pass(
                        prompt=ch_prompt,
                        negative_prompt=ch_negative,
                        loras=provider_loras,
                        init_image_bytes=current_image,
                        mask_bytes=mask_bytes,
                        strength=strength_by_idx[idx],
                        steps=settings.HQ_FG_STEPS,
                        params=inpaint_params,
                        timeout_s=per_pass_timeout,
                    )
                    current_image = next_image
                    successful_inpaints += 1
                    if used not in providers_attempted:
                        providers_attempted.append(used)
                except _AllInpaintProvidersFailed as exc:
                    # Track the failure but keep going — for multi-char,
                    # we may still produce a useful partial result with
                    # other characters rendered. If none succeed at all
                    # we fail the job below rather than silently shipping
                    # a backdrop with no character (Codex P1 review).
                    failed_inpaints += 1
                    inpaint_failure_reason = str(exc)
                    logger.warning(
                        "hq_inpaint_pass_failed",
                        job_id=str(job_id),
                        idx=idx + 1,
                        error=str(exc),
                    )
                    break

            # If no character pass succeeded, the only thing in current_image
            # is the backdrop — that's not the HQ render the user asked for.
            # Surface as a failed job with explicit code, instead of returning
            # a misleading "completed" state. Multi-char calls keep partial
            # results when at least one character rendered.
            if successful_inpaints == 0 and failed_inpaints > 0:
                raise _AllInpaintProvidersFailed(
                    inpaint_failure_reason or "all character inpaint passes failed"
                )

            # Pass N+1 — optional harmonization (only meaningful with adjacent
            # slots, so 3+ characters by default and at least 2 successful
            # inpaints — no point harmonizing seams that don't exist).
            do_harmonize = (
                settings.HQ_HARMONIZE_ENABLED
                and num_chars >= 3
                and successful_inpaints >= 2
            )
            if do_harmonize:
                await channel.publish(
                    job_id,
                    {
                        "event": "image_progress",
                        "data": {"job_id": str(job_id), "stage": "harmonize"},
                    },
                )
                seam_bytes = generate_seam_mask(canvas_w, canvas_h, scaled_slots)
                try:
                    current_image = await _run_harmonize_pass(
                        composite_bytes=current_image,
                        seam_mask=seam_bytes,
                        prompt=(
                            f"{job.prompt_user.strip()}, consistent lighting, "
                            "unified color palette, photorealistic"
                        ),
                        timeout_s=per_pass_timeout,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "hq_harmonize_failed",
                        job_id=str(job_id),
                        error=str(exc),
                    )

            # Persist final image + complete.
            storage = get_storage()
            url = await storage.save(job_id, idx=0, data=current_image, ext="png")
            output = ImageOutput(
                job_id=job_id,
                storage_url=url,
                mime_type="image/png",
                bytes_size=len(current_image),
                width=canvas_w,
                height=canvas_h,
            )
            db.add(output)
            job.status = ImageJobStatus.completed
            job.provider_used = "hq_multichar"
            job.providers_attempted = providers_attempted
            job.composite_meta = {
                **(job.composite_meta or {}),
                "layout": layout_name,
                "num_characters": num_chars,
                "successful_inpaints": successful_inpaints,
                "failed_inpaints": failed_inpaints,
                "harmonized": do_harmonize,
                "canvas": [canvas_w, canvas_h],
            }
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()

            total_passes = (
                1  # backdrop
                + successful_inpaints
                + (1 if do_harmonize else 0)
            )
            await channel.publish(
                job_id,
                {
                    "event": "image_completed",
                    "data": {
                        "job_id": str(job_id),
                        "provider": "hq_multichar",
                        "cache_hit": False,
                        "outputs": [{"storage_url": url}],
                        "hq": {
                            "layout": layout_name,
                            "passes": total_passes,
                            "successful_inpaints": successful_inpaints,
                            "failed_inpaints": failed_inpaints,
                        },
                    },
                },
            )
        except _AllInpaintProvidersFailed as exc:
            # All character inpaint passes failed — the backdrop alone is
            # not the HQ render the user requested.
            job.status = ImageJobStatus.failed
            job.error_code = "hq_no_inpaint_provider"
            job.error_message = str(exc)
            await db.commit()
            await channel.publish(
                job_id,
                {
                    "event": "image_failed",
                    "data": {
                        "job_id": str(job_id),
                        "error": str(exc),
                        "code": "hq_no_inpaint_provider",
                    },
                },
            )
        except AllProvidersFailedError as exc:
            job.status = ImageJobStatus.failed
            job.error_code = "hq_pass_failed"
            job.error_message = exc.last_error
            job.providers_attempted = exc.attempted
            await db.commit()
            await channel.publish(
                job_id,
                {
                    "event": "image_failed",
                    "data": {"job_id": str(job_id), "error": exc.last_error, "code": "hq_pass_failed"},
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("hq_job_error", job_id=str(job_id))
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


class _AllInpaintProvidersFailed(Exception):
    """No configured provider supports inpaint or all of them failed for a
    specific pass. Distinct from ``AllProvidersFailedError`` so the HQ flow
    can fall back to a partial result rather than failing the whole job.
    """


async def _run_inpaint_pass(
    *,
    prompt: str,
    negative_prompt: str,
    loras: list[dict[str, Any]],
    init_image_bytes: bytes,
    mask_bytes: bytes,
    strength: float,
    steps: int,
    params: dict[str, Any],
    timeout_s: int,
) -> tuple[bytes, str]:
    """Run an inpaint pass over the provider chain, returning (bytes, provider).

    Skips providers that don't implement ``generate_inpaint``; advances on
    transient errors. Raises ``_AllInpaintProvidersFailed`` if none succeed.
    """
    last_error = "no inpaint-capable provider"
    for name in settings.image_provider_order_list or ["dummy"]:
        cls = _PROVIDER_FACTORY.get(name)
        if cls is None:
            continue
        provider = cls()
        method = getattr(provider, "generate_inpaint", None)
        if method is None:
            continue
        try:
            data = await asyncio.wait_for(
                method(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    loras=loras,
                    init_image_bytes=init_image_bytes,
                    mask_bytes=mask_bytes,
                    strength=strength,
                    steps=steps,
                    params=params,
                ),
                timeout=timeout_s,
            )
            return data, provider.name
        except NotImplementedError:
            continue
        except asyncio.TimeoutError:
            last_error = f"{provider.name} timeout after {timeout_s}s"
            logger.warning("hq_inpaint_timeout", provider=provider.name)
            continue
        except Exception as exc:  # noqa: BLE001
            last_error = f"{provider.name}: {exc!r}"
            logger.warning("hq_inpaint_failed", provider=provider.name, error=last_error)
            continue
    raise _AllInpaintProvidersFailed(last_error)


async def _run_harmonize_pass(
    *,
    composite_bytes: bytes,
    seam_mask: bytes,
    prompt: str,
    timeout_s: int,
) -> bytes:
    """Whole-image low-strength img2img to smooth seams between inpainted chars.

    Falls back to ``generate_img2img`` (which doesn't take a mask) when no
    provider supports masked harmonization — providers like Runware accept
    masks via ``generate_inpaint`` so we try that first.
    """
    for name in settings.image_provider_order_list or ["dummy"]:
        cls = _PROVIDER_FACTORY.get(name)
        if cls is None:
            continue
        provider = cls()
        # Prefer mask-aware inpaint for harmonization (preserves character
        # regions); fall back to plain img2img on the whole image.
        inpaint = getattr(provider, "generate_inpaint", None)
        if inpaint is not None:
            try:
                return await asyncio.wait_for(
                    inpaint(
                        prompt=prompt,
                        negative_prompt="",
                        loras=[],
                        init_image_bytes=composite_bytes,
                        mask_bytes=seam_mask,
                        strength=settings.HQ_HARMONIZE_STRENGTH,
                        steps=settings.HQ_HARMONIZE_STEPS,
                        params={},
                    ),
                    timeout=timeout_s,
                )
            except NotImplementedError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("hq_harmonize_inpaint_failed", provider=name, error=str(exc))
        img2img = getattr(provider, "generate_img2img", None)
        if img2img is not None:
            try:
                return await asyncio.wait_for(
                    img2img(
                        prompt=prompt,
                        negative_prompt="",
                        init_image_bytes=composite_bytes,
                        strength=settings.HQ_HARMONIZE_STRENGTH,
                        steps=settings.HQ_HARMONIZE_STEPS,
                    ),
                    timeout=timeout_s,
                )
            except NotImplementedError:
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("hq_harmonize_img2img_failed", provider=name, error=str(exc))
                continue
    return composite_bytes


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
