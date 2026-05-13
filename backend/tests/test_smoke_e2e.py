"""End-to-end smoke test: register → login → character → chat → image job.

Story-AI streaming is skipped here because the test environment has no
Anthropic key and we don't mock the SDK; the image path exercises the
DummyProvider end-to-end so the SSE / DB / static-URL plumbing is real.
"""

from __future__ import annotations

import asyncio


async def _register_and_login(client, username: str) -> None:
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "supersecret123",
            "display_name": "Smoke Tester",
        },
    )
    assert res.status_code == 201, res.text

    res = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "supersecret123"},
    )
    assert res.status_code == 200, res.text


async def test_auth_round_trip(client, unique_username: str) -> None:
    await _register_and_login(client, unique_username)

    res = await client.get("/api/v1/auth/me")
    assert res.status_code == 200
    body = res.json()
    assert body["username"] == unique_username


async def test_characters_and_chat_creation(client, unique_username: str) -> None:
    await _register_and_login(client, unique_username)

    # Create character
    res = await client.post(
        "/api/v1/characters",
        json={
            "name": "Lyra Nightwhisper",
            "description": "A shadowy bard from the eastern wilds.",
            "personality": "Witty, observant.",
            "system_prompt": "You are Lyra Nightwhisper.",
            "tags": ["bard", "neutral"],
        },
    )
    assert res.status_code == 201, res.text
    character_id = res.json()["id"]

    # List characters
    res = await client.get("/api/v1/characters")
    assert res.status_code == 200
    assert any(c["id"] == character_id for c in res.json())

    # Create chat
    res = await client.post(
        "/api/v1/chats",
        json={"character_id": character_id, "title": "Encounter at the tavern"},
    )
    assert res.status_code == 201, res.text
    chat_id = res.json()["id"]

    # List chats
    res = await client.get("/api/v1/chats")
    assert res.status_code == 200
    assert any(c["id"] == chat_id for c in res.json())


async def test_image_job_happy_path(client, unique_username: str, tmp_path) -> None:
    """Generate → SSE completes → output row + storage URL present."""
    # Force storage to a fresh tmp dir.
    import importlib
    from app.core import config as config_mod
    from app.modules.images import storage as storage_mod

    config_mod.settings.IMAGES_STORAGE_DIR = str(tmp_path / "images")
    storage_mod._storage = None  # reset singleton so it picks up new dir
    importlib.reload(storage_mod)  # ensure module-level singleton refreshes

    await _register_and_login(client, unique_username)

    res = await client.post(
        "/api/v1/images/generate",
        json={"prompt": "a cozy tavern at midnight, lanterns glowing"},
    )
    assert res.status_code == 201, res.text
    job = res.json()
    assert job["status"] in ("queued", "running", "completed")
    job_id = job["id"]

    # Poll until terminal (background task should complete fast with DummyProvider).
    for _ in range(50):
        await asyncio.sleep(0.05)
        res = await client.get(f"/api/v1/images/jobs/{job_id}")
        assert res.status_code == 200
        status = res.json()["status"]
        if status in ("completed", "failed", "canceled"):
            break

    body = res.json()
    assert body["status"] == "completed", body
    assert body["provider_used"] == "dummy"
    assert len(body["outputs"]) == 1
    assert body["outputs"][0]["storage_url"].startswith("/static/images/")


async def test_image_endpoint_requires_auth(client) -> None:
    res = await client.post(
        "/api/v1/images/generate", json={"prompt": "anonymous attempt"}
    )
    assert res.status_code == 401
