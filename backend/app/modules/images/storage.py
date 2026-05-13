"""Image storage abstraction.

Pluggable backend selected by ``settings.STORAGE_BACKEND``:

* ``local`` — writes to ``settings.IMAGES_STORAGE_DIR`` and serves via the
  FastAPI ``/static/images`` mount.
* ``s3`` — S3-compatible (AWS S3, Cloudflare R2, Hetzner Storage Box). The
  ``boto3`` dependency is loaded lazily; if it isn't installed the factory
  falls back to local storage with a warning.

All backends produce a publicly servable URL via ``save`` and accept that URL
back for ``download`` / ``download_key``. The Tier 3 composer needs to pull
backdrops and poses by URL during runtime composition.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Protocol

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


class Storage(Protocol):
    name: str

    async def save(self, job_id: uuid.UUID, idx: int, data: bytes, ext: str = "png") -> str:
        """Persist bytes and return a publicly servable URL."""
        ...

    async def save_key(self, key: str, data: bytes, content_type: str = "image/png") -> str:
        """Persist bytes at a stable key (used by asset workers). Returns URL."""
        ...

    async def download(self, url_or_key: str) -> bytes:
        """Read bytes back from a URL (or key) previously returned by save."""
        ...


# ── Local filesystem backend ────────────────────────────────────────────────


class LocalStorage:
    """Writes files to ``IMAGES_STORAGE_DIR/<key>`` and returns
    ``/static/images/<key>`` URLs. Caller mounts a StaticFiles route on
    ``settings.IMAGES_STORAGE_DIR``."""

    name = "local"

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or settings.IMAGES_STORAGE_DIR)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, job_id: uuid.UUID, idx: int, data: bytes, ext: str = "png") -> str:
        job_dir = self.base_dir / str(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        path = job_dir / f"{idx}.{ext}"
        path.write_bytes(data)
        return f"/static/images/{job_id}/{idx}.{ext}"

    async def save_key(self, key: str, data: bytes, content_type: str = "image/png") -> str:
        safe_key = key.lstrip("/")
        path = self.base_dir / safe_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"/static/images/{safe_key}"

    async def download(self, url_or_key: str) -> bytes:
        prefix = "/static/images/"
        key = url_or_key[len(prefix):] if url_or_key.startswith(prefix) else url_or_key.lstrip("/")
        return (self.base_dir / key).read_bytes()


# ── S3-compatible backend (lazy boto3) ─────────────────────────────────────


class S3CompatibleStorage:
    """S3 / R2 / Hetzner Storage Box backend.

    Configured via ``S3_ENDPOINT``, ``S3_ACCESS_KEY``, ``S3_SECRET_KEY``,
    ``S3_BUCKET``. Optional ``S3_PUBLIC_BASE_URL`` lets you serve from a CDN
    instead of the raw endpoint.

    ``boto3`` is intentionally imported lazily because the dev environment
    can run without it; we only require it when the user opts into S3.
    """

    name = "s3"

    def __init__(self) -> None:
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:  # noqa: F841
            raise RuntimeError(
                "S3 storage requested but boto3 is not installed. "
                "`pip install boto3` or set STORAGE_BACKEND=local."
            ) from exc

        if not settings.S3_BUCKET or not settings.S3_ENDPOINT:
            raise RuntimeError(
                "S3 storage requires S3_BUCKET and S3_ENDPOINT settings."
            )

        self._bucket = settings.S3_BUCKET
        self._public_base = settings.S3_PUBLIC_BASE_URL or settings.S3_ENDPOINT.rstrip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
        )

    def _public_url(self, key: str) -> str:
        return f"{self._public_base.rstrip('/')}/{self._bucket}/{key.lstrip('/')}"

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        # boto3 sync API — wrap in to_thread when called from async paths.
        self._client.put_object(
            Bucket=self._bucket, Key=key.lstrip("/"), Body=data, ContentType=content_type
        )

    def _get(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self._bucket, Key=key.lstrip("/"))
        return obj["Body"].read()

    async def save(self, job_id: uuid.UUID, idx: int, data: bytes, ext: str = "png") -> str:
        import asyncio

        key = f"jobs/{job_id}/{idx}.{ext}"
        await asyncio.to_thread(self._put, key, data, f"image/{ext}")
        return self._public_url(key)

    async def save_key(self, key: str, data: bytes, content_type: str = "image/png") -> str:
        import asyncio

        await asyncio.to_thread(self._put, key, data, content_type)
        return self._public_url(key)

    async def download(self, url_or_key: str) -> bytes:
        import asyncio

        # Accept either a public URL or a raw key.
        key = url_or_key
        prefix = f"{self._public_base.rstrip('/')}/{self._bucket}/"
        if url_or_key.startswith(prefix):
            key = url_or_key[len(prefix):]
        return await asyncio.to_thread(self._get, key)


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is not None:
        return _storage

    backend = (settings.STORAGE_BACKEND or "local").lower()
    if backend == "s3":
        try:
            _storage = S3CompatibleStorage()
            logger.info("storage_backend", backend="s3", endpoint=settings.S3_ENDPOINT)
            return _storage
        except Exception as exc:  # noqa: BLE001
            logger.error("storage_s3_init_failed_fallback_local", error=str(exc))

    _storage = LocalStorage()
    logger.info("storage_backend", backend="local", base=str(_storage.base_dir))
    return _storage


def reset_storage_for_tests() -> None:
    global _storage
    _storage = None
