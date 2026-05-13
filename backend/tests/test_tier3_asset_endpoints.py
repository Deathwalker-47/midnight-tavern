"""Phase 2 tests for the Tier 3 asset library list endpoints.

Exercises ``GET /api/v1/images/backdrops`` (with tag filter) and
``GET /api/v1/images/characters/{id}/poses`` (with pose/expression/facing
filters) against an empty DB and a seeded DB.
"""

from __future__ import annotations

import uuid

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
    assert res.status_code == 201, res.text
    res = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "supersecret123"},
    )
    assert res.status_code == 200, res.text


async def _create_character(client) -> str:
    res = await client.post(
        "/api/v1/characters",
        json={
            "name": "Test Character",
            "description": "x",
            "personality": "x",
            "system_prompt": "x",
            "tags": [],
        },
    )
    assert res.status_code == 201
    return res.json()["id"]


@pytest.mark.asyncio
async def test_backdrops_empty_returns_empty_list(client, unique_username):
    await _register_and_login(client, unique_username)
    res = await client.get("/api/v1/images/backdrops")
    assert res.status_code == 200
    assert res.json() == []


@pytest.mark.asyncio
async def test_backdrops_seeded_and_tag_filter(client, unique_username, session_maker):
    await _register_and_login(client, unique_username)

    from app.modules.images.models import Backdrop

    async with session_maker() as session:
        session.add_all(
            [
                Backdrop(
                    slug="tavern_a",
                    description="warm tavern",
                    tags=["indoor", "tavern", "warm-light"],
                    storage_url="/static/images/backdrops/tavern_a.png",
                    lighting_profile={"direction": "left", "warmth": "warm", "intensity": "soft"},
                    aspect_ratio="3:2",
                ),
                Backdrop(
                    slug="forest_a",
                    description="forest path",
                    tags=["outdoor", "forest"],
                    storage_url="/static/images/backdrops/forest_a.png",
                    lighting_profile={"direction": "right", "warmth": "warm"},
                    aspect_ratio="3:2",
                ),
            ]
        )
        await session.commit()

    res = await client.get("/api/v1/images/backdrops")
    assert res.status_code == 200
    payload = res.json()
    assert {b["slug"] for b in payload} == {"tavern_a", "forest_a"}

    res = await client.get("/api/v1/images/backdrops", params={"tag": "tavern"})
    assert {b["slug"] for b in res.json()} == {"tavern_a"}

    res = await client.get("/api/v1/images/backdrops", params={"tag": "outdoor"})
    assert {b["slug"] for b in res.json()} == {"forest_a"}

    res = await client.get(
        "/api/v1/images/backdrops", params=[("tag", "indoor"), ("tag", "outdoor")]
    )
    assert {b["slug"] for b in res.json()} == {"tavern_a", "forest_a"}


@pytest.mark.asyncio
async def test_character_poses_filter(client, unique_username, session_maker):
    await _register_and_login(client, unique_username)
    character_id = uuid.UUID(await _create_character(client))

    from app.modules.images.models import CharacterPose

    async with session_maker() as session:
        for pose in ("standing_neutral", "sitting_relaxed"):
            for facing in ("forward", "three_quarter_left"):
                session.add(
                    CharacterPose(
                        character_id=character_id,
                        lora_version="v1",
                        pose_slug=pose,
                        expression_slug="neutral",
                        framing="full_body",
                        facing=facing,
                        transparent_storage_url=f"/static/images/poses/{pose}_{facing}.png",
                        width=1024,
                        height=1024,
                        bounding_box={"x": 0, "y": 0, "w": 800, "h": 1000},
                        lighting_profile={},
                    )
                )
        await session.commit()

    res = await client.get(f"/api/v1/images/characters/{character_id}/poses")
    assert res.status_code == 200
    assert len(res.json()) == 4

    res = await client.get(
        f"/api/v1/images/characters/{character_id}/poses",
        params={"pose_slug": "standing_neutral"},
    )
    assert {p["facing"] for p in res.json()} == {"forward", "three_quarter_left"}

    res = await client.get(
        f"/api/v1/images/characters/{character_id}/poses",
        params={"facing": "forward"},
    )
    assert {p["pose_slug"] for p in res.json()} == {"standing_neutral", "sitting_relaxed"}

    res = await client.get(
        f"/api/v1/images/characters/{character_id}/poses",
        params={"lora_version": "vNONE"},
    )
    assert res.json() == []
