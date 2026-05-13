"""End-to-end Tier 3 composite endpoint test.

Seeds a backdrop + character poses, requests a composite, polls until the
job completes, asserts the SceneComposite cache row is written, then
re-requests with the same inputs and confirms the second request hits cache.

DummyProvider's ``generate_img2img`` returns the input bytes unchanged so the
pipeline completes end-to-end without real provider credits.
"""

from __future__ import annotations

import asyncio
import io
import uuid

import pytest
from PIL import Image


def _png_bytes(w: int = 256, h: int = 256) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (180, 180, 180, 255)).save(buf, format="PNG")
    return buf.getvalue()


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


async def _create_character(client, name: str) -> str:
    res = await client.post(
        "/api/v1/characters",
        json={
            "name": name,
            "description": "x",
            "personality": "x",
            "system_prompt": "x",
            "tags": [],
        },
    )
    assert res.status_code == 201
    return res.json()["id"]


async def _seed_backdrop_and_poses(
    session_maker, tmp_path, character_ids: list[uuid.UUID]
) -> None:
    """Seed: one backdrop + one pose per character. Also writes the actual
    image bytes to the local storage dir so the composer can download them."""
    from app.core import config as config_mod
    from app.modules.images.models import Backdrop, CharacterPose
    from app.modules.images.storage import LocalStorage

    config_mod.settings.IMAGES_STORAGE_DIR = str(tmp_path)
    storage = LocalStorage(base_dir=str(tmp_path))

    backdrop_url = await storage.save_key("backdrops/test.png", _png_bytes(1536, 1024))
    pose_url = await storage.save_key("poses/test.png", _png_bytes(600, 1000))

    async with session_maker() as session:
        session.add(
            Backdrop(
                slug="test_tavern",
                description="warm tavern",
                tags=["indoor", "tavern", "warm-light"],
                storage_url=backdrop_url,
                lighting_profile={"warmth": "warm"},
                aspect_ratio="3:2",
            )
        )
        for cid in character_ids:
            session.add(
                CharacterPose(
                    character_id=cid,
                    lora_version="v1",
                    pose_slug="standing_neutral",
                    expression_slug="neutral",
                    framing="full_body",
                    facing="forward",
                    transparent_storage_url=pose_url,
                    width=600,
                    height=1000,
                    bounding_box={"x": 0, "y": 0, "w": 600, "h": 1000},
                    lighting_profile={},
                )
            )
        await session.commit()


async def _poll_until_terminal(client, job_id: str, max_attempts: int = 50) -> dict:
    for _ in range(max_attempts):
        res = await client.get(f"/api/v1/images/jobs/{job_id}")
        assert res.status_code == 200
        body = res.json()
        if body["status"] in ("completed", "failed", "canceled"):
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached terminal state")


@pytest.mark.asyncio
async def test_composite_endpoint_happy_path(
    client, unique_username, session_maker, tmp_path
):
    # Force local storage to a fresh dir + ensure storage singleton refreshes.
    from app.modules.images import storage as storage_mod
    storage_mod.reset_storage_for_tests()

    await _register_and_login(client, unique_username)
    cid_a = uuid.UUID(await _create_character(client, "Char A"))
    cid_b = uuid.UUID(await _create_character(client, "Char B"))

    await _seed_backdrop_and_poses(session_maker, tmp_path, [cid_a, cid_b])

    res = await client.post(
        "/api/v1/images/composite",
        json={
            "scene_description": "warm tavern interior, the two of them by the fire",
            "character_ids": [str(cid_a), str(cid_b)],
        },
    )
    assert res.status_code == 201, res.text
    job = res.json()
    assert job["job_kind"] == "composite"

    final = await _poll_until_terminal(client, job["id"])
    assert final["status"] == "completed", final
    assert final["outputs"], "expected at least one composite output"
    assert final["composite_meta"]["layout"] == "side_by_side"

    # Second identical request should hit the SceneComposite cache.
    res2 = await client.post(
        "/api/v1/images/composite",
        json={
            "scene_description": "warm tavern interior, the two of them by the fire",
            "character_ids": [str(cid_a), str(cid_b)],
        },
    )
    assert res2.status_code == 201
    job2 = res2.json()
    final2 = await _poll_until_terminal(client, job2["id"])
    assert final2["status"] == "completed"
    assert final2["provider_used"] == "composite_cache"


@pytest.mark.asyncio
async def test_composite_endpoint_rejects_zero_characters(client, unique_username):
    await _register_and_login(client, unique_username)
    res = await client.post(
        "/api/v1/images/composite",
        json={"scene_description": "empty room", "character_ids": []},
    )
    assert res.status_code == 422  # Pydantic min_length validation


@pytest.mark.asyncio
async def test_composite_endpoint_rejects_unowned_character(
    client, unique_username, session_maker, tmp_path
):
    """Codex P1: composite endpoint must reject character_ids the caller can't see."""
    from app.modules.characters.models import Character
    from app.modules.images import storage as storage_mod

    storage_mod.reset_storage_for_tests()

    await _register_and_login(client, unique_username)
    my_char = uuid.UUID(await _create_character(client, "Mine"))

    # Seed a private character owned by a different user.
    other_user_id = uuid.uuid4()
    other_char_id = uuid.uuid4()
    async with session_maker() as session:
        session.add(
            Character(
                id=other_char_id,
                user_id=other_user_id,
                name="Not Mine",
                description="x",
                personality="x",
                system_prompt="x",
                tags=[],
                is_public=False,
            )
        )
        await session.commit()

    # Seed assets so the request would otherwise succeed.
    await _seed_backdrop_and_poses(session_maker, tmp_path, [my_char, other_char_id])

    res = await client.post(
        "/api/v1/images/composite",
        json={
            "scene_description": "tavern at night",
            "character_ids": [str(my_char), str(other_char_id)],
        },
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "character_not_found"


@pytest.mark.asyncio
async def test_composite_endpoint_rejects_duplicate_character_ids(
    client, unique_username
):
    await _register_and_login(client, unique_username)
    cid = await _create_character(client, "Char")
    res = await client.post(
        "/api/v1/images/composite",
        json={
            "scene_description": "twin scene",
            "character_ids": [cid, cid],
        },
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "duplicate_character_id"
