"""Local-filesystem storage for image outputs.

Designed as a Protocol so a future S3/R2 backend slots in without touching
service code. Phase 1 only ships the local backend.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

from app.core.config import settings


class Storage(Protocol):
    async def save(self, job_id: uuid.UUID, idx: int, data: bytes, ext: str = "png") -> str:  # noqa: D401
        """Persist bytes and return a publicly servable URL."""
        ...


class LocalStorage:
    """Writes files to ``IMAGES_STORAGE_DIR/{job_id}/{idx}.{ext}`` and returns
    a ``/static/images/...`` URL. Caller mounts a StaticFiles route on
    ``settings.IMAGES_STORAGE_DIR``."""

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or settings.IMAGES_STORAGE_DIR)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, job_id: uuid.UUID, idx: int, data: bytes, ext: str = "png") -> str:
        job_dir = self.base_dir / str(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        path = job_dir / f"{idx}.{ext}"
        path.write_bytes(data)
        return f"/static/images/{job_id}/{idx}.{ext}"


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = LocalStorage()
    return _storage
