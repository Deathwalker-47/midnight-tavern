"""LocalStorage save/save_key/download round-trip + S3 selection failure mode."""

from __future__ import annotations

import uuid

import pytest

from app.modules.images.storage import LocalStorage, get_storage, reset_storage_for_tests


@pytest.mark.asyncio
async def test_local_storage_save_and_download(tmp_path):
    storage = LocalStorage(base_dir=str(tmp_path))
    job_id = uuid.uuid4()
    url = await storage.save(job_id, 0, b"PNGDATA")
    assert url == f"/static/images/{job_id}/0.png"
    assert await storage.download(url) == b"PNGDATA"


@pytest.mark.asyncio
async def test_local_storage_save_key(tmp_path):
    storage = LocalStorage(base_dir=str(tmp_path))
    url = await storage.save_key("backdrops/tavern_a.png", b"ABC")
    assert url == "/static/images/backdrops/tavern_a.png"
    assert (tmp_path / "backdrops" / "tavern_a.png").read_bytes() == b"ABC"
    assert await storage.download(url) == b"ABC"


def test_s3_backend_falls_back_to_local_when_misconfigured(monkeypatch, tmp_path):
    """If STORAGE_BACKEND=s3 but creds missing, fall back to LocalStorage."""
    from app.core import config as config_mod

    reset_storage_for_tests()
    monkeypatch.setattr(config_mod.settings, "STORAGE_BACKEND", "s3", raising=False)
    monkeypatch.setattr(config_mod.settings, "S3_BUCKET", None, raising=False)
    monkeypatch.setattr(config_mod.settings, "S3_ENDPOINT", None, raising=False)
    monkeypatch.setattr(config_mod.settings, "IMAGES_STORAGE_DIR", str(tmp_path), raising=False)

    storage = get_storage()
    assert storage.name == "local"
    reset_storage_for_tests()
