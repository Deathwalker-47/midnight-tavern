"""Backdrop pre-generation worker.

Reads ``backend/config/backdrop_specs.yaml``, generates each backdrop via the
image provider chain (no character LoRAs), uploads to storage, indexes a
``Backdrop`` row. Idempotent on ``slug`` — re-running skips existing slugs
unless ``regenerate=True``.

Two entrypoints:

* ARQ task: ``generate_backdrops(ctx, *, regenerate=False)`` — wired in
  ``WorkerSettings.functions``.
* CLI: ``python -m app.workers.cli.run_worker backdrops [--regenerate]``.

The provider chain runs at module-level, identical to ``service.run_job`` —
no character LoRAs are matched because we don't pass a chat or character
context. Backdrops are intentionally character-free so they composite
cleanly under any pose.
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
from app.modules.images.models import Backdrop
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


def _load_specs(specs_path: str | Path | None = None) -> list[dict[str, Any]]:
    path = Path(specs_path or settings.BACKDROP_SPECS_PATH)
    with path.open("r", encoding="utf-8") as f:
        payload = yaml.safe_load(f)
    return payload.get("backdrops", [])


def _dims_for_aspect(aspect: str) -> tuple[int, int]:
    if aspect == "3:2":
        return 1536, 1024
    if aspect == "16:9":
        return 1536, 864
    if aspect == "1:1":
        return 1024, 1024
    if aspect == "4:3":
        return 1280, 960
    return 1536, 1024


async def generate_backdrops(
    ctx: dict | None = None,
    *,
    regenerate: bool = False,
    specs_path: str | None = None,
) -> dict[str, int]:
    """Generate every backdrop in the spec file. Returns counts."""
    specs = _load_specs(specs_path)
    chain = _build_chain()
    storage = get_storage()
    counts = {"created": 0, "skipped": 0, "failed": 0}

    async with AsyncSessionLocal() as db:
        for spec in specs:
            slug = spec["slug"]
            existing = await db.execute(select(Backdrop).where(Backdrop.slug == slug))
            row = existing.scalar_one_or_none()
            if row is not None and not regenerate:
                counts["skipped"] += 1
                continue

            width, height = _dims_for_aspect(spec.get("aspect_ratio", "3:2"))
            try:
                result = await chain.generate(
                    spec["prompt"],
                    "",
                    [],
                    {
                        "steps": 30,
                        "cfg_scale": 4,
                        "width": width,
                        "height": height,
                        "seed": -1,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("backdrop_generate_failed", slug=slug, error=str(exc))
                counts["failed"] += 1
                continue

            key = f"backdrops/{slug}.png"
            url = await storage.save_key(key, result.image_bytes)

            if row is None:
                row = Backdrop(
                    id=uuid.uuid4(),
                    slug=slug,
                    description=spec["prompt"],
                    tags=spec.get("tags", []),
                    width=width,
                    height=height,
                    storage_url=url,
                    lighting_profile=spec.get("lighting_profile", {}),
                    aspect_ratio=spec.get("aspect_ratio", "3:2"),
                    generation_meta={"provider": result.provider_used},
                )
                db.add(row)
                counts["created"] += 1
            else:
                row.storage_url = url
                row.description = spec["prompt"]
                row.tags = spec.get("tags", [])
                row.lighting_profile = spec.get("lighting_profile", {})
                row.aspect_ratio = spec.get("aspect_ratio", "3:2")
                row.generation_meta = {"provider": result.provider_used}
                counts["created"] += 1  # treat regen as created
            await db.commit()

    logger.info("backdrop_generation_complete", **counts)
    return counts
