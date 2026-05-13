"""Tier 3 composite job orchestrator.

Flow (matches §4.4 of the image generation plan):

1. Load the ``ImageJob`` row (must be ``job_kind = composite``).
2. Resolve characters → list of UUIDs in declared order.
3. Compute the scene hash early — but we don't have backdrop_id/pose_ids yet,
   so cache lookup happens after step 5.
4. Classify the scene with ``scene_classifier`` → tags + per-character pose
   recommendations.
5. Pick a backdrop by tag overlap; pick a CharacterPose row per character via
   (pose_slug, expression_slug, facing) lookup with fuzzy fallback.
6. ``scene_composites`` cache lookup by hash → on hit, short-circuit.
7. PIL compose backdrop + poses into ``composite_v0``.
8. Lighting unification img2img pass over composite_v0 (if a provider supports
   it). On any failure here, ship ``composite_v0`` rather than blocking — the
   pre-blend is still usable.
9. Store, write scene_composites row, publish SSE.

If anything in steps 4–8 fails the job is marked ``failed`` and an
``image_failed`` SSE event is published.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select

from app.modules.characters.models import Character
from app.modules.images.composer import (
    LAYOUT_NAMES,
    PoseSlot,
    compose_scene,
    pick_layout,
    scene_hash,
)
from app.modules.images.models import (
    Backdrop,
    CharacterPose,
    ImageJob,
    ImageJobStatus,
    ImageOutput,
    SceneComposite,
)
from app.modules.images.scene_classifier import (
    PoseRecommendation,
    SceneClassification,
    classify_scene,
)
from app.modules.images.service import channel
from app.modules.images.storage import get_storage

logger = structlog.get_logger(__name__)


async def _pick_backdrop(db, tags: list[str]) -> Backdrop | None:
    """Rank backdrops by tag overlap. Pick the top match; ties resolved by slug."""
    result = await db.execute(select(Backdrop))
    rows = list(result.scalars().all())
    if not rows:
        return None
    wanted = {t.lower() for t in tags}

    def overlap(b: Backdrop) -> int:
        return len(wanted.intersection({t.lower() for t in (b.tags or [])}))

    ranked = sorted(rows, key=lambda b: (-overlap(b), b.slug))
    top = ranked[0]
    return top if overlap(top) > 0 else rows[0]


async def _pick_pose(
    db,
    character_id: uuid.UUID,
    rec: PoseRecommendation,
) -> CharacterPose | None:
    """Lookup the pose by exact match; fall back to same-pose any-expression.

    A single character can have rows under multiple ``lora_version`` tags
    (after retraining). We always prefer the newest row, so the lookup orders
    by ``generated_at DESC`` and takes the first match — using
    ``scalar_one_or_none`` here would raise ``MultipleResultsFound`` and the
    composite job would fail spuriously.
    """
    query = (
        select(CharacterPose)
        .where(
            CharacterPose.character_id == character_id,
            CharacterPose.pose_slug == rec.pose_slug,
            CharacterPose.expression_slug == rec.expression_slug,
            CharacterPose.facing == rec.facing,
        )
        .order_by(CharacterPose.generated_at.desc())
    )
    result = await db.execute(query)
    pose = result.scalars().first()
    if pose is not None:
        return pose
    # Same pose, any expression/facing.
    query = (
        select(CharacterPose)
        .where(
            CharacterPose.character_id == character_id,
            CharacterPose.pose_slug == rec.pose_slug,
        )
        .order_by(CharacterPose.generated_at.desc())
    )
    result = await db.execute(query)
    pose = result.scalars().first()
    if pose is not None:
        return pose
    # Any pose for this character — last resort.
    result = await db.execute(
        select(CharacterPose)
        .where(CharacterPose.character_id == character_id)
        .order_by(CharacterPose.generated_at.desc())
    )
    return result.scalars().first()


async def _try_img2img(
    composite_bytes: bytes, prompt: str, lighting: dict[str, Any]
) -> bytes:
    """Run the lighting unification img2img pass.

    Tries the providers in order until one supports ``generate_img2img``.
    Returns the unified bytes on success, otherwise returns the input bytes
    unchanged (with a warning logged).
    """
    from app.core.config import settings
    from app.modules.images.providers import (
        DummyProvider,
        FALProvider,
        RunwareProvider,
        WavespeedProvider,
    )

    factory: dict[str, Any] = {
        "dummy": DummyProvider,
        "runware": RunwareProvider,
        "wavespeed": WavespeedProvider,
        "fal": FALProvider,
    }
    for name in settings.image_provider_order_list or ["dummy"]:
        cls = factory.get(name)
        if cls is None:
            continue
        provider = cls()
        if not hasattr(provider, "generate_img2img"):
            continue
        try:
            return await provider.generate_img2img(  # type: ignore[attr-defined]
                prompt=prompt,
                negative_prompt="",
                init_image_bytes=composite_bytes,
                strength=settings.COMPOSITE_LIGHTING_STRENGTH,
                steps=settings.COMPOSITE_LIGHTING_STEPS,
                lighting=lighting,
            )
        except NotImplementedError:
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("img2img_pass_failed", provider=name, error=str(exc))
            continue
    logger.info("img2img_pass_skipped_no_provider")
    return composite_bytes


async def run_composite_job(job_id: uuid.UUID) -> None:
    """Execute a queued composite job."""
    # Lazy-import so the conftest's ``monkeypatch.setattr(session_module,
    # "AsyncSessionLocal", session_maker)`` is honored on each test (the
    # alternative — caching the symbol at module import — leaves a stale
    # binding once the first test's fixture tears down).
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ImageJob).where(ImageJob.id == job_id))
        job = result.scalar_one_or_none()
        if job is None:
            logger.warning("composite_job_missing", job_id=str(job_id))
            return

        meta = job.composite_meta or {}
        scene_description = meta.get("scene_description") or job.prompt_user
        character_ids_raw = meta.get("character_ids") or []
        character_ids = [uuid.UUID(cid) for cid in character_ids_raw]
        if not character_ids:
            await _fail(db, job, "no_characters", "composite job has no character_ids")
            return

        try:
            job.status = ImageJobStatus.running
            await db.commit()
            await channel.publish(
                job_id,
                {"event": "image_started", "data": {"job_id": str(job_id), "kind": "composite"}},
            )

            classification: SceneClassification = await classify_scene(
                scene_description, [str(cid) for cid in character_ids]
            )

            backdrop = await _pick_backdrop(db, classification.tags)
            if backdrop is None:
                await _fail(db, job, "no_backdrops", "backdrop library is empty")
                return

            # Index classifier output by character_id — LLM order isn't
            # guaranteed to match the request order (Codex review P2). For any
            # character the classifier omitted, fall back to a neutral pose
            # recommendation so we still produce a usable composite.
            rec_by_cid: dict[str, PoseRecommendation] = {
                rec.character_id: rec for rec in classification.poses
            }
            poses: list[CharacterPose] = []
            for cid in character_ids:
                rec = rec_by_cid.get(str(cid)) or PoseRecommendation(
                    character_id=str(cid),
                    pose_slug="standing_neutral",
                    expression_slug="neutral",
                    facing="forward",
                )
                pose = await _pick_pose(db, cid, rec)
                if pose is None:
                    await _fail(
                        db, job, "no_poses_for_character",
                        f"no CharacterPose rows for character {cid}",
                    )
                    return
                poses.append(pose)

            layout_name, _ = pick_layout(len(poses))
            sh = scene_hash(backdrop.id, [p.id for p in poses], layout_name)

            cache_lookup = await db.execute(
                select(SceneComposite).where(SceneComposite.scene_hash == sh)
            )
            cached = cache_lookup.scalar_one_or_none()
            if cached is not None:
                cached.last_used_at = datetime.now(timezone.utc)
                await db.commit()
                await _complete(
                    db,
                    job,
                    storage_url=cached.storage_url,
                    cache_hit=True,
                    composite_meta={
                        "backdrop_id": str(backdrop.id),
                        "pose_ids": [str(p.id) for p in poses],
                        "layout": layout_name,
                        "tags": classification.tags,
                        "classifier": classification.method,
                        "scene_hash": sh,
                    },
                )
                return

            await channel.publish(
                job_id,
                {"event": "image_progress", "data": {"job_id": str(job_id), "stage": "composing"}},
            )

            storage = get_storage()
            backdrop_bytes = await storage.download(backdrop.storage_url)
            pose_slots: list[PoseSlot] = []
            for pose in poses:
                pose_bytes = await storage.download(pose.transparent_storage_url)
                pose_slots.append(
                    PoseSlot(pose_bytes=pose_bytes, bounding_box=pose.bounding_box)
                )

            composite_v0 = compose_scene(
                backdrop_bytes,
                pose_slots,
                target_width=job.width,
                target_height=job.height,
            )

            await channel.publish(
                job_id,
                {"event": "image_progress", "data": {"job_id": str(job_id), "stage": "unifying"}},
            )
            unification_prompt = (
                f"{scene_description}, unified cohesive lighting, "
                f"{backdrop.lighting_profile.get('warmth', 'neutral')} light, "
                "photorealistic, cinematic"
            )
            final_bytes = await _try_img2img(
                composite_v0, unification_prompt, backdrop.lighting_profile
            )

            key = f"composites/{sh[:32]}.png"
            storage_url = await storage.save_key(key, final_bytes)

            db.add(
                SceneComposite(
                    id=uuid.uuid4(),
                    scene_hash=sh,
                    backdrop_id=backdrop.id,
                    pose_ids=[str(p.id) for p in poses],
                    layout_template=layout_name,
                    storage_url=storage_url,
                    composite_meta={
                        "scene_description": scene_description,
                        "tags": classification.tags,
                        "classifier": classification.method,
                    },
                )
            )
            await _complete(
                db,
                job,
                storage_url=storage_url,
                cache_hit=False,
                composite_meta={
                    "backdrop_id": str(backdrop.id),
                    "pose_ids": [str(p.id) for p in poses],
                    "layout": layout_name,
                    "tags": classification.tags,
                    "classifier": classification.method,
                    "scene_hash": sh,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("composite_job_error", job_id=str(job_id))
            await _fail(db, job, "internal_error", str(exc))


async def _complete(
    db,
    job: ImageJob,
    *,
    storage_url: str,
    cache_hit: bool,
    composite_meta: dict[str, Any],
) -> None:
    output = ImageOutput(
        job_id=job.id,
        storage_url=storage_url,
        mime_type="image/png",
        width=job.width,
        height=job.height,
    )
    db.add(output)
    job.status = ImageJobStatus.completed
    job.provider_used = "composite_cache" if cache_hit else "composite"
    job.providers_attempted = ["composite_cache"] if cache_hit else ["composite"]
    job.completed_at = datetime.now(timezone.utc)
    job.composite_meta = composite_meta
    await db.commit()

    await channel.publish(
        job.id,
        {
            "event": "image_completed",
            "data": {
                "job_id": str(job.id),
                "provider": job.provider_used,
                "cache_hit": cache_hit,
                "outputs": [{"storage_url": storage_url}],
                "composite": composite_meta,
            },
        },
    )


async def _fail(db, job: ImageJob, code: str, message: str) -> None:
    job.status = ImageJobStatus.failed
    job.error_code = code
    job.error_message = message
    await db.commit()
    await channel.publish(
        job.id,
        {"event": "image_failed", "data": {"job_id": str(job.id), "error": message, "code": code}},
    )
