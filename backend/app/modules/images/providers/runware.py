"""Runware provider — ported from Silly-Tavern-Flux-Bridge's ``RunwareClient``
(flux_lora_bridge.py:812). The auto-upload-to-Runware-AIR-ID step is skipped:
we pass through whatever URL/AIR-id is stored in the LoRA dictionary, which
matches the bridge's behavior when ``runware_lora_mapping.json`` already
contains the entry.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog

from app.core.config import settings
from app.modules.images.providers.base import (
    ImageProviderError,
    extract_image_bytes,
    validate_image_bytes,
)

logger = structlog.get_logger(__name__)


class RunwareProvider:
    name = "runware"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.RUNWARE_API_KEY
        self.model = model or settings.RUNWARE_MODEL
        self.endpoint = "https://api.runware.ai/v1"

    async def generate(
        self,
        prompt: str,
        negative_prompt: str,
        loras: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> bytes:
        if not self.api_key:
            raise ImageProviderError("RUNWARE_API_KEY not configured", transient=True)

        lora_payload = [{"model": l["url"], "weight": l.get("weight", 1.0)} for l in loras]

        task: dict[str, Any] = {
            "taskType": "imageInference",
            "taskUUID": str(uuid.uuid4()),
            "positivePrompt": prompt,
            "negativePrompt": negative_prompt,
            "model": self.model,
            "steps": params.get("steps", 20),
            "CFGScale": params.get("cfg_scale", 3.5),
            "height": params.get("height", 1024),
            "width": params.get("width", 1024),
            "numberResults": 1,
            "outputFormat": "jpg",
            "lora": lora_payload,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(self.endpoint, json=[task], headers=headers)
        if resp.status_code != 200:
            raise ImageProviderError(
                f"Runware API {resp.status_code}: {resp.text[:200]}", transient=True
            )
        result = resp.json()
        image_bytes = await extract_image_bytes(result, "Runware")
        validate_image_bytes(image_bytes, "Runware")
        return image_bytes
