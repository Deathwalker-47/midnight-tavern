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
