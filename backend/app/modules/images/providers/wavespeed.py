"""Wavespeed provider — ported from Silly-Tavern-Flux-Bridge's
``WavespeedClient`` (flux_lora_bridge.py:899). Handles both immediate and
queued/polling responses.
"""

from __future__ import annotations

import asyncio
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

MAX_LORAS = 4
POLL_INTERVAL_S = 2.0
POLL_MAX_ATTEMPTS = 60  # 120s total

ENDPOINT = "https://api.wavespeed.ai/api/v3/wavespeed-ai/flux-dev-lora"


class WavespeedProvider:
    name = "wavespeed"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.WAVESPEED_API_KEY

    async def generate(
        self,
        prompt: str,
        negative_prompt: str,
        loras: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> bytes:
        if not self.api_key:
            raise ImageProviderError("WAVESPEED_API_KEY not configured", transient=True)

        loras = loras[:MAX_LORAS]
        lora_list = [{"path": l["url"], "scale": l.get("weight", 1.0)} for l in loras]
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "prompt": f"{prompt}. Negative: {negative_prompt}" if negative_prompt else prompt,
            "loras": lora_list,
            "num_inference_steps": params.get("steps", 20),
            "guidance_scale": params.get("cfg_scale", 3.5),
            "width": params.get("width", 1024),
            "height": params.get("height", 1024),
            "seed": params.get("seed", -1),
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(ENDPOINT, json=payload, headers=headers)
        if resp.status_code != 200:
            raise ImageProviderError(
                f"Wavespeed API {resp.status_code}: {resp.text[:200]}", transient=True
            )
        result = resp.json()
        data = result.get("data", result)

        # Immediate response
        if isinstance(data, dict) and data.get("outputs"):
            image_bytes = await extract_image_bytes(result, "Wavespeed")
            validate_image_bytes(image_bytes, "Wavespeed")
            return image_bytes

        # Polling
        poll_url = (data.get("urls") or {}).get("get") if isinstance(data, dict) else None
        if not poll_url:
            raise ImageProviderError(
                "Wavespeed: no outputs and no polling URL", transient=True
            )

        async with httpx.AsyncClient(timeout=30.0) as client:
            for _ in range(POLL_MAX_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL_S)
                poll_resp = await client.get(poll_url, headers=headers)
                if poll_resp.status_code != 200:
                    continue
                inner = poll_resp.json().get("data", poll_resp.json())
                status = inner.get("status", "")
                if status in ("processing", "created", "pending", "in_queue"):
                    continue
                if status == "failed":
                    raise ImageProviderError(
                        f"Wavespeed job failed: {inner.get('error', 'unknown')}",
                        transient=True,
                    )
                if inner.get("outputs"):
                    image_bytes = await extract_image_bytes(inner, "Wavespeed")
                    validate_image_bytes(image_bytes, "Wavespeed")
                    return image_bytes

        raise ImageProviderError("Wavespeed polling timed out after 120s", transient=True)
