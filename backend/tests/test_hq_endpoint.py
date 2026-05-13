"""Phase 5 HQ endpoint test.

Until ``MultiCharPipeline`` is ported, the HQ path falls back to the single-
character generator. The endpoint contract is still exercised: POST returns
a job with ``job_kind == "hq"``, the SSE lifecycle reaches terminal, and the
output URL is recorded.
"""

from __future__ import annotations

import asyncio

import pytest


async def _register_and_login(client, username: str) -> None:
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "supersecret123",
            "display_name": "Tester",
        },
    )
    assert res.status_code == 201
    res = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "supersecret123"},
    )
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_hq_endpoint_returns_job_with_kind(client, unique_username, tmp_path):
    from app.core import config as config_mod
    from app.modules.images import storage as storage_mod

    config_mod.settings.IMAGES_STORAGE_DIR = str(tmp_path / "hq-imgs")
    storage_mod.reset_storage_for_tests()

    await _register_and_login(client, unique_username)

    res = await client.post(
        "/api/v1/images/generate_hq",
        json={"prompt": "two adventurers in a tavern, cinematic, painterly"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["job_kind"] == "hq"
    job_id = body["id"]

    # Poll until completed via the standard jobs endpoint.
    for _ in range(60):
        res = await client.get(f"/api/v1/images/jobs/{job_id}")
        assert res.status_code == 200
        body = res.json()
        if body["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.05)
    assert body["status"] == "completed", body
    assert body["job_kind"] == "hq"
    assert body["outputs"], "expected at least one HQ output"
