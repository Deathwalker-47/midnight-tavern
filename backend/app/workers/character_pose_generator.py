"""Character pose pre-generation worker.

Reads ``backend/config/pose_matrix.yaml``, iterates (pose × expression ×
facing) combinations for a single character LoRA, generates each pose against
a neutral grey backdrop, runs background removal, stores the transparent PNG,
and indexes a ``CharacterPose`` row.

``lora_version`` enables cache invalidation. When a character LoRA is
retrained, the caller bumps the version and runs the worker again — old rows
remain accessible for in-flight composites but new lookups prefer the new
version.

ARQ task: ``generate_character_poses(ctx, character_id, lora_version)``.
CLI: ``python -m app.workers.cli.run_worker poses --character <uuid> --lora-version v1``.

The provider chain is the same one used by ``service.run_job`` — the character
LoRA is matched via the existing keyword pipeline by including the character's
keyword inside ``prompt_fragment`` (the ``{trigger}`` placeholder).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import structlog
import yaml
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.modules.characters.models import Character
from app.modules.images.background_removal import get_background_remover
from app.modules.images.lora import (
    PROVIDER_MAX_LORAS,
    LoRAManager,
    get_lora_manager,
)
from app.modules.images.models import CharacterPose
from app.modules.images.providers import (
    DummyProvider,
    FALProvider,
    ProviderChain,
    RunwareProvider,
    WavespeedProvider,
)
from app.modules.images.storage import get_storage

logger = structlog.get_logger(__name__)


_PROVIDER_FACTORY: dict[str, Any] = {
    "dummy": DummyProvider,
    "runware": RunwareProvider,
    "wavespeed": WavespeedProvider,
    "fal": FALProvider,
}


def _build_chain() -> ProviderChain:
    providers = []
    for name in settings.image_provider_order_list or ["dummy"]:
        factory = _PROVIDER_FACTORY.get(name)
        if factory is not None:
            providers.append(factory())
    if not providers:
        providers.append(DummyProvider())
    return ProviderChain(providers)


def _load_matrix(matrix_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(matrix_path or settings.POSE_MATRIX_PATH)
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_trigger(character: Character) -> str:
    """Pull the keyword to feed into ``{trigger}`` substitution.

    Defaults to the character's name; if a more specific keyword maps to a
    LoRA in ``master_lora_dict.json``, we use that instead since the LoRA
    matcher will activate on it.
    """
    mgr = get_lora_manager()
    name = character.name.lower()
    if mgr is not None:
        for lid, data in mgr._dict.get("loras", {}).items():  # noqa: SLF001
            if data.get("category") != "character":
                continue
            for kw in data.get("keywords", []):
                if kw.lower() in name:
                    return kw
    return character.name


async def generate_character_poses(
    ctx: dict | None = None,
    *,
    character_id: uuid.UUID,
    lora_version: str,
    matrix_path: str | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    matrix = _load_matrix(matrix_path)
    poses = matrix.get("poses", [])
    expressions = matrix.get("expressions", [])
    gen = matrix.get("generation", {})
    neutral_backdrop = gen.get(
        "neutral_backdrop_prompt",
        "neutral grey studio backdrop, flat soft lighting",
    )
    width = int(gen.get("width", 1024))
    height = int(gen.get("height", 1024))
    steps = int(gen.get("steps", 30))
    cfg = int(gen.get("cfg_scale", 4))

    counts = {"created": 0, "skipped": 0, "failed": 0}
    chain = _build_chain()
    storage = get_storage()
    remover = None  # Lazy: only instantiated when we actually need it.

    async with AsyncSessionLocal() as db:
        character = await db.get(Character, character_id)
        if character is None:
            raise ValueError(f"Character {character_id} not found")

        trigger = _build_trigger(character)
        produced = 0

        for pose in poses:
            for expression in expressions:
                for facing in pose.get("facings", ["forward"]):
                    framing = (pose.get("framings") or ["full_body"])[0]
                    if limit is not None and produced >= limit:
                        logger.info("pose_generation_limit_reached", produced=produced)
                        return counts

                    existing = await db.execute(
                        select(CharacterPose).where(
                            CharacterPose.character_id == character_id,
                            CharacterPose.lora_version == lora_version,
                            CharacterPose.pose_slug == pose["slug"],
                            CharacterPose.expression_slug == expression["slug"],
                            CharacterPose.facing == facing,
                        )
                    )
                    if existing.scalar_one_or_none() is not None:
                        counts["skipped"] += 1
                        continue

                    fragment = pose["prompt_fragment"].format(trigger=trigger)
                    expr_text = expression["prompt_fragment"]
                    prompt = (
                        f"{fragment}, {expr_text}, {facing} facing, {framing} framing, "
                        f"isolated subject, {neutral_backdrop}"
                    )

                    mgr = get_lora_manager()
                    matched = mgr.match(prompt) if mgr is not None else []
                    matched = LoRAManager.apply_role_caps(matched)
                    provider_loras = LoRAManager.build_lora_list(
                        matched,
                        max_loras=PROVIDER_MAX_LORAS.get("runware", 12),
                    )

                    try:
                        result = await chain.generate(
                            prompt,
                            "",
                            provider_loras,
                            {
                                "steps": steps,
                                "cfg_scale": cfg,
                                "width": width,
                                "height": height,
                                "seed": -1,
                            },
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.error(
                            "pose_generate_failed",
                            character_id=str(character_id),
                            pose=pose["slug"],
                            expression=expression["slug"],
                            facing=facing,
                            error=str(exc),
                        )
                        counts["failed"] += 1
                        continue

                    # Background removal.
                    if remover is None:
                        try:
                            remover = get_background_remover()
                        except Exception as exc:  # noqa: BLE001
                            logger.error("background_remover_init_failed", error=str(exc))
                            counts["failed"] += 1
                            continue
                    try:
                        transparent_bytes, bbox = await remover.remove(result.image_bytes)
                    except Exception as exc:  # noqa: BLE001
                        logger.error(
                            "background_removal_failed",
                            character_id=str(character_id),
                            error=str(exc),
                        )
                        counts["failed"] += 1
                        continue

                    base_key = (
                        f"poses/{character_id}/{lora_version}/"
                        f"{pose['slug']}_{expression['slug']}_{facing}"
                    )
                    raw_url = await storage.save_key(
                        f"{base_key}_raw.png", result.image_bytes
                    )
                    transparent_url = await storage.save_key(
                        f"{base_key}.png", transparent_bytes
                    )

                    db.add(
                        CharacterPose(
                            id=uuid.uuid4(),
                            character_id=character_id,
                            lora_version=lora_version,
                            pose_slug=pose["slug"],
                            expression_slug=expression["slug"],
                            framing=framing,
                            facing=facing,
                            transparent_storage_url=transparent_url,
                            raw_storage_url=raw_url,
                            width=width,
                            height=height,
                            bounding_box=bbox,
                            lighting_profile={
                                "direction": "flat",
                                "warmth": "neutral",
                                "intensity": "medium",
                            },
                            generation_meta={
                                "provider": result.provider_used,
                                "prompt": prompt,
                            },
                        )
                    )
                    await db.commit()
                    counts["created"] += 1
                    produced += 1

    logger.info("pose_generation_complete", **counts)
    return counts
