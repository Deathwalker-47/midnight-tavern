"""Request-level image cache.

Key: SHA-256 of (prompt, negative, sorted LoRA url/weight tuples, deterministic
params). Value: the storage URL of a previously generated image.

Random seeds (``seed == -1`` or ``None``) skip cache entirely — the whole point
of a random seed is non-determinism. Deterministic seeds hit the cache.

Two backends:
* ``RedisImageCache`` — used when ``REDIS_URL`` is reachable. TTL enforced.
* ``InMemoryImageCache`` — dev fallback, bounded LRU dict. Process-local.

``get_image_cache()`` returns a process-wide singleton chosen by
``settings.IMAGE_REQUEST_CACHE_BACKEND`` (``auto`` resolves to Redis if a
client can be built, else memory).
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any, Protocol

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


def make_cache_key(
    prompt: str,
    negative_prompt: str,
    loras: list[dict[str, Any]],
    params: dict[str, Any],
) -> str | None:
    """Return the cache key, or ``None`` when the request must skip cache.

    Skip rules: random seed (``-1`` or ``None``).
    """
    seed = params.get("seed")
    if seed is None or seed == -1:
        return None

    lora_sig = sorted(
        ((str(lora.get("url", "")), float(lora.get("weight", 1.0))) for lora in loras),
        key=lambda x: (x[0], x[1]),
    )
    param_sig = {
        k: params.get(k)
        for k in ("steps", "cfg_scale", "width", "height", "seed")
        if params.get(k) is not None
    }
    payload = json.dumps(
        {"p": prompt, "n": negative_prompt, "l": lora_sig, "params": param_sig},
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"mt:img:{digest}"


class ImageRequestCache(Protocol):
    name: str

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str) -> None: ...


class InMemoryImageCache:
    """Bounded LRU dict. Process-local, lost on restart. Dev/test only."""

    name = "memory"

    def __init__(self, max_entries: int = 2048) -> None:
        self._store: OrderedDict[str, str] = OrderedDict()
        self._max = max_entries

    async def get(self, key: str) -> str | None:
        value = self._store.get(key)
        if value is not None:
            self._store.move_to_end(key)
        return value

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value
        self._store.move_to_end(key)
        while len(self._store) > self._max:
            self._store.popitem(last=False)


class RedisImageCache:
    """Redis-backed cache. TTL from ``settings.IMAGE_REQUEST_CACHE_TTL_S``."""

    name = "redis"

    def __init__(self, client: Any, ttl_seconds: int) -> None:
        self._client = client
        self._ttl = ttl_seconds

    async def get(self, key: str) -> str | None:
        value = await self._client.get(key)
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    async def set(self, key: str, value: str) -> None:
        await self._client.set(key, value, ex=self._ttl)


_cache: ImageRequestCache | None = None


def _try_build_redis_cache() -> ImageRequestCache | None:
    try:
        import redis.asyncio as redis_asyncio  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("image_cache_redis_unavailable", reason="redis package not installed")
        return None
    try:
        client = redis_asyncio.from_url(settings.REDIS_URL, decode_responses=False)
        return RedisImageCache(client, settings.IMAGE_REQUEST_CACHE_TTL_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning("image_cache_redis_init_failed", error=str(exc))
        return None


def get_image_cache() -> ImageRequestCache:
    """Return the process-wide cache, building it on first call."""
    global _cache
    if _cache is not None:
        return _cache

    backend = settings.IMAGE_REQUEST_CACHE_BACKEND.lower()
    if backend == "memory":
        _cache = InMemoryImageCache()
    elif backend == "redis":
        built = _try_build_redis_cache()
        _cache = built or InMemoryImageCache()
    else:  # auto
        built = _try_build_redis_cache()
        _cache = built or InMemoryImageCache()

    logger.info("image_cache_backend", backend=_cache.name)
    return _cache


def reset_image_cache_for_tests() -> None:
    """Drop the cached singleton so the next ``get_image_cache()`` rebuilds."""
    global _cache
    _cache = None
