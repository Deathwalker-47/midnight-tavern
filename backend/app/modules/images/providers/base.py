"""Image provider protocol + shared payload helpers.

Helpers (``_extract_image_candidate``, ``_resolve_image_bytes_from_payload``,
``_validate_image_bytes``) are ported verbatim from
Silly-Tavern-Flux-Bridge/flux_lora_bridge.py:744–806 with cosmetic renames so
they can be reused by every provider.
"""

from __future__ import annotations

import base64
from typing import Any, Protocol

import httpx


class ImageProviderError(Exception):
    """Provider-side failure. ``transient=True`` means the chain should
    advance to the next provider; ``False`` indicates a permanent failure
    (e.g. invalid API key) that still advances but flags the chain."""

    def __init__(self, message: str, *, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


class ImageProvider(Protocol):
    name: str

    async def generate(
        self,
        prompt: str,
        negative_prompt: str,
        loras: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> bytes: ...

    # Optional methods. Providers that don't implement them should raise
    # ``NotImplementedError`` so callers can fall back to the next provider
    # in the chain (matches the pattern used by ``generate_img2img``).

    async def generate_img2img(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        init_image_bytes: bytes,
        strength: float,
        steps: int,
        lighting: dict[str, Any] | None = None,
    ) -> bytes: ...

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
    ) -> bytes: ...


# ── Shared payload helpers (ported from flux_lora_bridge.py) ─────────────────


def _strip_data_uri_prefix(value: str) -> str:
    if isinstance(value, str) and value.startswith("data:") and "," in value:
        return value.split(",", 1)[1]
    return value


def _try_decode_base64(value: str) -> bytes | None:
    if not isinstance(value, str):
        return None
    candidate = _strip_data_uri_prefix(value).strip()
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return None
    try:
        return base64.b64decode(candidate, validate=True)
    except Exception:
        return None


def _extract_image_candidate(payload: Any) -> Any:
    if payload is None:
        return None
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        for item in payload:
            cand = _extract_image_candidate(item)
            if cand is not None:
                return cand
        return None
    if isinstance(payload, dict):
        direct_keys = (
            "imageURL",
            "image_url",
            "imageUrl",
            "image",
            "url",
            "b64_json",
            "base64",
        )
        for key in direct_keys:
            if key in payload and payload[key]:
                cand = _extract_image_candidate(payload[key])
                if cand is not None:
                    return cand
        container_keys = ("data", "output", "outputs", "images", "result", "results")
        for key in container_keys:
            if key in payload and payload[key] is not None:
                cand = _extract_image_candidate(payload[key])
                if cand is not None:
                    return cand
        for value in payload.values():
            cand = _extract_image_candidate(value)
            if cand is not None:
                return cand
    return None


async def extract_image_bytes(payload: Any, provider_name: str) -> bytes:
    """Walk a provider response and return raw image bytes.

    Handles bytes, http(s) URLs (downloaded with httpx), base64 (with or
    without data URI prefix), and nested ``data/outputs/images`` arrays.
    """
    candidate = _extract_image_candidate(payload)
    if candidate is None:
        raise ImageProviderError(
            f"[{provider_name}] No image candidate in payload", transient=True
        )

    if isinstance(candidate, (bytes, bytearray)):
        return bytes(candidate)

    if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(candidate)
            resp.raise_for_status()
            return resp.content

    decoded = _try_decode_base64(candidate) if isinstance(candidate, str) else None
    if decoded is not None:
        return decoded

    raise ImageProviderError(
        f"[{provider_name}] Unsupported image payload format", transient=True
    )


def validate_image_bytes(data: bytes, provider_name: str) -> None:
    """Reject obviously-not-an-image responses (HTML error pages, etc.).

    Ported from flux_lora_bridge.py:798.
    """
    if len(data) < 4:
        raise ImageProviderError(
            f"[{provider_name}] Image data too small ({len(data)} bytes)", transient=True
        )
    if not (
        data[:3] == b"\xff\xd8\xff"  # JPEG
        or data[:4] == b"\x89PNG"  # PNG
        or data[:4] == b"RIFF"  # WEBP
        or data[:4] == b"GIF8"  # GIF
    ):
        raise ImageProviderError(
            f"[{provider_name}] Downloaded data is not a valid image "
            f"(first bytes: {data[:16].hex()})",
            transient=True,
        )
