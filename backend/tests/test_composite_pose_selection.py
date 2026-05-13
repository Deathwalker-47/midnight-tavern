"""Regression tests for the composite_service pose-picking helpers.

Covers the two Codex review fixes:

* P1 — ``_pick_pose`` must tolerate multiple ``lora_version`` rows for the
  same character (post-retraining) and return the newest one.
* P2 — ``run_composite_job`` must match classifier recommendations to
  characters by ``character_id``, not by positional zip — because LLM
  output order isn't guaranteed.
"""

from __future__ import annotations

import uuid

import pytest

from app.modules.images.composite_service import _pick_pose
from app.modules.images.models import CharacterPose
from app.modules.images.scene_classifier import PoseRecommendation


@pytest.mark.asyncio
async def test_pick_pose_returns_newest_when_multiple_lora_versions(
    session_maker, engine
):
    """A retrained character has rows under multiple ``lora_version`` values
    with the same (pose, expression, facing). Picker must return the newest,
    not raise ``MultipleResultsFound``."""
    from app.modules.auth.models import User
    from app.modules.characters.models import Character
    import datetime as _dt

    user_id = uuid.uuid4()
    character_id = uuid.uuid4()

    async with session_maker() as session:
        session.add(
            User(
                id=user_id,
                username=f"u_{user_id.hex[:8]}",
                email=f"{user_id.hex[:8]}@e.com",
                hashed_password="x",
                is_active=True,
            )
        )
        session.add(
            Character(
                id=character_id,
                user_id=user_id,
                name="C",
                description="x",
                personality="x",
                system_prompt="x",
                tags=[],
                is_public=False,
            )
        )
        await session.commit()

        for version, ts in (
            ("v1", _dt.datetime(2024, 1, 1, tzinfo=_dt.timezone.utc)),
            ("v2", _dt.datetime(2025, 1, 1, tzinfo=_dt.timezone.utc)),
        ):
            session.add(
                CharacterPose(
                    character_id=character_id,
                    lora_version=version,
                    pose_slug="standing_neutral",
                    expression_slug="neutral",
                    framing="full_body",
                    facing="forward",
                    transparent_storage_url=f"/static/images/poses/{version}.png",
                    width=600,
                    height=1000,
                    bounding_box={"x": 0, "y": 0, "w": 600, "h": 1000},
                    lighting_profile={},
                    generated_at=ts,
                )
            )
        await session.commit()

    rec = PoseRecommendation(
        character_id=str(character_id),
        pose_slug="standing_neutral",
        expression_slug="neutral",
        facing="forward",
    )
    async with session_maker() as session:
        pose = await _pick_pose(session, character_id, rec)
    assert pose is not None
    assert pose.lora_version == "v2"


@pytest.mark.asyncio
async def test_pick_pose_falls_back_to_same_pose_when_no_exact_match(
    session_maker, engine
):
    """If no row matches (pose, expression, facing), the picker should still
    return the same-pose any-expression-any-facing fallback."""
    from app.modules.auth.models import User
    from app.modules.characters.models import Character

    user_id = uuid.uuid4()
    character_id = uuid.uuid4()
    async with session_maker() as session:
        session.add(
            User(
                id=user_id,
                username=f"u_{user_id.hex[:8]}",
                email=f"{user_id.hex[:8]}@e.com",
                hashed_password="x",
                is_active=True,
            )
        )
        session.add(
            Character(
                id=character_id,
                user_id=user_id,
                name="C",
                description="x",
                personality="x",
                system_prompt="x",
                tags=[],
                is_public=False,
            )
        )
        await session.commit()
        session.add(
            CharacterPose(
                character_id=character_id,
                lora_version="v1",
                pose_slug="standing_neutral",
                expression_slug="smiling",
                framing="full_body",
                facing="three_quarter_left",
                transparent_storage_url="/static/images/poses/fallback.png",
                width=600,
                height=1000,
                bounding_box={"x": 0, "y": 0, "w": 600, "h": 1000},
                lighting_profile={},
            )
        )
        await session.commit()

    rec = PoseRecommendation(
        character_id=str(character_id),
        pose_slug="standing_neutral",
        expression_slug="neutral",  # not present
        facing="forward",            # not present
    )
    async with session_maker() as session:
        pose = await _pick_pose(session, character_id, rec)
    assert pose is not None
    assert pose.pose_slug == "standing_neutral"


@pytest.mark.asyncio
async def test_compose_matches_recommendations_by_character_id_not_position(
    client, unique_username, session_maker, tmp_path
):
    """Codex P2: when the classifier emits recommendations in a different
    order than the request, the right pose is still chosen per character.

    We stub the classifier to return reversed-order recommendations with
    distinct ``pose_slug`` values, seed each character with only its
    expected pose, and assert the composite completes (which it only
    will if each character matched its OWN recommendation)."""
    import asyncio

    from app.modules.auth.models import User
    from app.modules.characters.models import Character
    from app.modules.images import composite_service, storage as storage_mod
    from app.modules.images.models import Backdrop, CharacterPose
    from app.modules.images.scene_classifier import (
        PoseRecommendation,
        SceneClassification,
    )

    storage_mod.reset_storage_for_tests()

    # Register caller + create two characters via API so they belong to user.
    async def _register(client, username: str) -> None:
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

    await _register(client, unique_username)
    res = await client.post(
        "/api/v1/characters",
        json={"name": "A", "description": "x", "personality": "x", "system_prompt": "x", "tags": []},
    )
    cid_a = uuid.UUID(res.json()["id"])
    res = await client.post(
        "/api/v1/characters",
        json={"name": "B", "description": "x", "personality": "x", "system_prompt": "x", "tags": []},
    )
    cid_b = uuid.UUID(res.json()["id"])

    # Seed assets. Critically, character A only has 'kneeling_one_knee' and
    # B only has 'sitting_relaxed' — positional zip would mis-pair them.
    from PIL import Image
    import io as _io

    def _png(w: int, h: int) -> bytes:
        b = _io.BytesIO()
        Image.new("RGBA", (w, h), (180, 180, 180, 255)).save(b, format="PNG")
        return b.getvalue()

    storage = storage_mod.LocalStorage(base_dir=str(tmp_path))
    bd_url = await storage.save_key("backdrops/test.png", _png(1536, 1024))
    pose_url_a = await storage.save_key("poses/a.png", _png(600, 1000))
    pose_url_b = await storage.save_key("poses/b.png", _png(600, 1000))
    # Point the running storage singleton at the same dir.
    from app.core import config as _cfg

    _cfg.settings.IMAGES_STORAGE_DIR = str(tmp_path)
    storage_mod.reset_storage_for_tests()

    async with session_maker() as session:
        session.add(
            Backdrop(
                slug="test_tavern_p2",
                description="warm tavern",
                tags=["indoor", "tavern"],
                storage_url=bd_url,
                lighting_profile={"warmth": "warm"},
                aspect_ratio="3:2",
            )
        )
        session.add(
            CharacterPose(
                character_id=cid_a,
                lora_version="v1",
                pose_slug="kneeling_one_knee",
                expression_slug="neutral",
                framing="full_body",
                facing="forward",
                transparent_storage_url=pose_url_a,
                width=600,
                height=1000,
                bounding_box={"x": 0, "y": 0, "w": 600, "h": 1000},
                lighting_profile={},
            )
        )
        session.add(
            CharacterPose(
                character_id=cid_b,
                lora_version="v1",
                pose_slug="sitting_relaxed",
                expression_slug="neutral",
                framing="waist_up",
                facing="forward",
                transparent_storage_url=pose_url_b,
                width=600,
                height=1000,
                bounding_box={"x": 0, "y": 0, "w": 600, "h": 1000},
                lighting_profile={},
            )
        )
        await session.commit()

    # Monkeypatch the classifier to return recs in REVERSED order vs request.
    async def fake_classify(description: str, character_ids: list[str], **_kw):
        return SceneClassification(
            tags=["indoor", "tavern"],
            poses=[
                PoseRecommendation(
                    character_id=str(cid_b),  # B first
                    pose_slug="sitting_relaxed",
                    expression_slug="neutral",
                    facing="forward",
                ),
                PoseRecommendation(
                    character_id=str(cid_a),  # A second
                    pose_slug="kneeling_one_knee",
                    expression_slug="neutral",
                    facing="forward",
                ),
            ],
            method="llm",
        )

    original = composite_service.classify_scene
    composite_service.classify_scene = fake_classify
    try:
        res = await client.post(
            "/api/v1/images/composite",
            json={
                "scene_description": "tavern scene",
                "character_ids": [str(cid_a), str(cid_b)],  # A, B request order
            },
        )
        assert res.status_code == 201, res.text
        job_id = res.json()["id"]

        for _ in range(60):
            res = await client.get(f"/api/v1/images/jobs/{job_id}")
            body = res.json()
            if body["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.05)
        assert body["status"] == "completed", body
        # composite_meta.pose_ids should list the poses in request order.
        meta = body["composite_meta"]
        assert len(meta["pose_ids"]) == 2
    finally:
        composite_service.classify_scene = original
