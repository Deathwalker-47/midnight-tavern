"""Background removal for character pose pre-generation.

The character-pose worker generates each pose against a neutral grey backdrop,
then runs background removal to produce a transparent PNG ready for runtime
composition over any backdrop.

Two backends:

* ``rembg`` — light, fast, decent edges. Default. Lazy import — only required
  when the worker actually runs.
* ``birefnet`` — higher-quality (especially on hair), heavier. Stubbed; flip
  ``settings.BACKGROUND_REMOVAL_BACKEND="birefnet"`` once the dep is wired.

Return value: ``(transparent_png_bytes, bounding_box_dict)`` where the bbox
captures the tight pixel rectangle of the subject within the image — used
later to scale the character into the layout slot.
"""

from __future__ import annotations

import io
from typing import Any, Protocol

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


class BackgroundRemover(Protocol):
    name: str

    async def remove(self, image_bytes: bytes) -> tuple[bytes, dict[str, int]]:
        ...


def _bbox_from_alpha(png_bytes: bytes) -> dict[str, int]:
    """Return ``{x, y, w, h}`` of the non-transparent region, or empty bbox."""
    from PIL import Image  # type: ignore[import-not-found]

    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    bbox = img.getbbox()
    if bbox is None:
        return {"x": 0, "y": 0, "w": img.width, "h": img.height}
    x0, y0, x1, y1 = bbox
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


class RembgRemover:
    name = "rembg"

    def __init__(self) -> None:
        try:
            from rembg import remove as _remove  # type: ignore[import-not-found]
        except ImportError as exc:  # noqa: F841
            raise RuntimeError(
                "rembg is required for background removal. "
                "Install `rembg` (and optionally `onnxruntime`) to run pose workers."
            ) from exc
        self._remove = _remove

    async def remove(self, image_bytes: bytes) -> tuple[bytes, dict[str, int]]:
        import asyncio

        # rembg is CPU-bound; offload to a thread.
        out = await asyncio.to_thread(self._remove, image_bytes)
        return out, _bbox_from_alpha(out)


class BiRefNetRemover:
    name = "birefnet"

    def __init__(self) -> None:
        raise NotImplementedError(
            "BiRefNet remover not wired yet. Use rembg for now."
        )

    async def remove(self, image_bytes: bytes) -> tuple[bytes, dict[str, int]]:
        raise NotImplementedError


def get_background_remover() -> BackgroundRemover:
    backend = (settings.BACKGROUND_REMOVAL_BACKEND or "rembg").lower()
    if backend == "rembg":
        return RembgRemover()
    if backend == "birefnet":
        return BiRefNetRemover()
    raise ValueError(f"Unknown background removal backend: {backend!r}")
