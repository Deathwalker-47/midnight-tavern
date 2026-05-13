"""Dummy provider for tests and local dev — returns a tiny PNG.

Lets the full pipeline (job → SSE → outputs → DB → static URL) be exercised
without any real API keys.
"""

from __future__ import annotations

import base64
from typing import Any

# 1×1 transparent PNG.
_TINY_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class DummyProvider:
    name = "dummy"

    async def generate(
        self,
        prompt: str,
        negative_prompt: str,
        loras: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> bytes:
        return _TINY_PNG

    async def generate_img2img(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        init_image_bytes: bytes,
        strength: float,
        steps: int,
        lighting: dict[str, Any] | None = None,
    ) -> bytes:
        """No-op: returns the init bytes unchanged so the composite pipeline
        completes end-to-end in tests."""
        return init_image_bytes

    async def generate_inpaint(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        loras: list[dict[str, Any]],
        init_image_bytes: bytes,
        mask_bytes: bytes,
        strength: float,
        steps: int,
        params: dict[str, Any],
    ) -> bytes:
        """No-op inpaint: returns init bytes unchanged so the HQ pipeline
        completes end-to-end in tests. The mask is ignored — the test counts
        the number of inpaint calls, not the resulting pixels.
        """
        return init_image_bytes
