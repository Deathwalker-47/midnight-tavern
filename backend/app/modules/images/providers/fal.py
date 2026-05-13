"""FAL.ai provider — ported from Silly-Tavern-Flux-Bridge's ``FALClient``
(flux_lora_bridge.py:1005). FAL's queue API responds with ``response_url`` /
``status_url`` that we poll until completion.
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

MAX_LORAS = 3
POLL_INTERVAL_S = 2.0
POLL_MAX_ATTEMPTS = 60

ENDPOINT = "https://queue.fal.run/fal-ai/flux-lora"


class FALProvider:
    name = "fal"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.FAL_API_KEY

    async def generate(
        self,
        prompt: str,
        negative_prompt: str,
        loras: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> bytes:
        if not self.api_key:
            raise ImageProviderError("FAL_API_KEY not configured", transient=True)

        loras = loras[:MAX_LORAS]
        lora_list = [{"path": l["url"], "scale": l.get("weight", 1.0)} for l in loras]
        width = params.get("width", 1024)
        height = params.get("height", 1024)
        if width == height == 1024:
            image_size = "square_hd"
        elif width > height:
            image_size = "landscape_16_9"
        else:
            image_size = "portrait_16_9"

        payload = {
            "prompt": prompt,
            "image_size": image_size,
            "num_inference_steps": params.get("steps", 40),
            "guidance_scale": params.get("cfg_scale", 3.5),
            "num_images": 1,
            "enable_safety_checker": False,
            "loras": lora_list,
        }
        headers = {
            "Authorization": f"Key {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(ENDPOINT, json=payload, headers=headers)
        if resp.status_code != 200:
            raise ImageProviderError(
                f"FAL API {resp.status_code}: {resp.text[:200]}", transient=True
            )
        result = resp.json()

        if "images" in result or (
            isinstance(result.get("data"), dict) and "images" in result["data"]
        ):
            image_bytes = await extract_image_bytes(result, "FAL")
            validate_image_bytes(image_bytes, "FAL")
            return image_bytes

        response_url = result.get("response_url")
        status_url = result.get("status_url")
        if not response_url:
            raise ImageProviderError(
                f"FAL returned no images and no response_url: {list(result.keys())}",
                transient=True,
            )

        async with httpx.AsyncClient(timeout=30.0) as client:
            for _ in range(POLL_MAX_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL_S)
                if status_url:
                    status_resp = await client.get(status_url, headers=headers)
                    if status_resp.status_code == 200:
                        st = status_resp.json().get("status", "")
                        if st in ("IN_QUEUE", "IN_PROGRESS"):
                            continue
                poll_resp = await client.get(response_url, headers=headers)
                if poll_resp.status_code == 200:
                    poll_data = poll_resp.json()
                    if "images" in poll_data or (
                        isinstance(poll_data.get("data"), dict)
                        and "images" in poll_data.get("data", {})
                    ):
                        image_bytes = await extract_image_bytes(poll_data, "FAL")
                        validate_image_bytes(image_bytes, "FAL")
                        return image_bytes
                elif poll_resp.status_code == 202:
                    continue

        raise ImageProviderError("FAL polling timed out after 120s", transient=True)
