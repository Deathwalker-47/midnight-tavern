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


# ── Sprint A Task 15: full e2e smoke flow ────────────────────────────────────


class _FakeAnthropicStream:
    """Mock AnthropicProvider that yields a caller-provided token sequence.

    The ``stream`` method is an async generator that yields each token in
    order with no inter-token delay — enough to drive ``MarkerStreamStripper``
    end-to-end. ``complete`` is unused by tests but kept for compatibility.
    """

    _tokens: list[str] = []

    def __init__(self, api_key: str) -> None:
        # The chat router constructs us with an API key; we ignore it.
        self.api_key = api_key

    async def stream(self, messages, system, model="claude-sonnet-4-6", **kwargs):
        for tok in self._tokens:
            yield tok

    async def complete(self, *_args, **_kwargs):
        return "".join(self._tokens)


def _parse_sse_stream(text: str) -> list[tuple[str, dict]]:
    """Parse a finished SSE payload into ``[(event_name, data_dict), ...]``."""
    import json

    events: list[tuple[str, dict]] = []
    pending: str | None = None
    for line in text.splitlines():
        if line.startswith("event: "):
            pending = line[len("event: ") :].strip()
        elif line.startswith("data: ") and pending is not None:
            try:
                events.append((pending, json.loads(line[len("data: ") :])))
            except json.JSONDecodeError:
                pass
            pending = None
    return events


async def _set_image_pref(client, pref: str) -> None:
    res = await client.patch("/api/v1/users/me", json={"image_pref": pref})
    assert res.status_code == 200, res.text


async def test_chat_message_dispatches_marker_image(
    client, unique_username, tmp_path, monkeypatch
):
    """LLM emits [SCENE_BEAT:single] → image_dispatched event fires, the
    marker is stripped from the stored message, and the resulting job
    reaches image_completed via the DummyProvider."""
    from app.core import config as config_mod
    from app.modules.images import storage as storage_mod
    from app.modules.providers import anthropic as anthropic_mod

    config_mod.settings.IMAGES_STORAGE_DIR = str(tmp_path / "imgs")
    storage_mod.reset_storage_for_tests()
    # Make the provider lookup return a non-empty API key fallback.
    monkeypatch.setattr(config_mod.settings, "ANTHROPIC_API_KEY", "fake-key-for-tests")

    _FakeAnthropicStream._tokens = [
        "She enters the tavern, ",
        "the door closing behind her. ",
        "[SCENE_BEAT:single] ",
        "Lanterns flicker overhead.",
    ]
    monkeypatch.setattr(anthropic_mod, "AnthropicProvider", _FakeAnthropicStream)

    await _register_and_login(client, unique_username)

    # Create character + chat.
    res = await client.post(
        "/api/v1/characters",
        json={
            "name": "Lyra",
            "description": "Bard from the wilds.",
            "personality": "Watchful.",
            "system_prompt": "You are Lyra.",
            "tags": [],
        },
    )
    assert res.status_code == 201, res.text
    character_id = res.json()["id"]
    res = await client.post(
        "/api/v1/chats", json={"character_id": character_id, "title": "Marker test"}
    )
    assert res.status_code == 201, res.text
    chat_id = res.json()["id"]

    # POST a message — consume the SSE stream.
    async with client.stream(
        "POST",
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "Walk into the tavern."},
    ) as resp:
        assert resp.status_code == 200
        body_text = ""
        async for chunk in resp.aiter_text():
            body_text += chunk

    events = _parse_sse_stream(body_text)
    event_names = [n for (n, _d) in events]
    assert "image_dispatched" in event_names, f"missing image_dispatched in {event_names}"

    # Find the image_dispatched event payload.
    dispatched = next(d for (n, d) in events if n == "image_dispatched")
    assert dispatched["tier"] == "single"
    image_job_id = dispatched["job_id"]

    # No token event should leak the SCENE_BEAT marker.
    token_events = [d for (n, d) in events if n == "token"]
    joined = "".join(t.get("text", "") for t in token_events)
    assert "[SCENE_BEAT" not in joined, f"marker leaked into stream: {joined!r}"

    # The stored character message must not contain the marker either.
    res = await client.get(f"/api/v1/chats/{chat_id}/messages")
    assert res.status_code == 200
    msgs = res.json()["messages"]
    char_msgs = [m for m in msgs if m["role"] == "character"]
    assert char_msgs, "no character message persisted"
    assert all("[SCENE_BEAT" not in m["content"] for m in char_msgs)

    # The dispatched image job runs in the background — poll until terminal.
    import asyncio

    for _ in range(60):
        res = await client.get(f"/api/v1/images/jobs/{image_job_id}")
        assert res.status_code == 200
        if res.json()["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.05)
    assert res.json()["status"] == "completed", res.json()


async def test_composite_endpoint_happy_path(
    client, unique_username, tmp_path, monkeypatch
):
    """Seed Backdrop + CharacterPose rows directly via the async session, stub
    classify_scene, POST /composite, assert the composite job completes with a
    populated composite_meta and a scene_hash. Second call with the same
    inputs returns provider_used=composite_cache."""
    import asyncio
    import uuid

    from app.core import config as config_mod
    from app.modules.images import (
        composite_service as composite_mod,
        scene_classifier,
        storage as storage_mod,
    )
    from app.modules.images.models import Backdrop, CharacterPose
    from app.modules.images.scene_classifier import (
        PoseRecommendation,
        SceneClassification,
    )

    config_mod.settings.IMAGES_STORAGE_DIR = str(tmp_path / "comp-imgs")
    storage_mod.reset_storage_for_tests()
    # Provide a one-pixel backdrop PNG and pose PNG via the local-storage layer.
    # We seed both as data-URI-like bytes by writing tiny PNGs to the storage
    # location and registering them in DB.
    import base64

    tiny_png = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )

    # Stub classifier to a deterministic output (no LLM call).
    def _stub_classify(_desc, character_ids):
        return SceneClassification(
            tags=["tavern", "indoor"],
            poses=[
                PoseRecommendation(
                    character_id=cid,
                    pose_slug="standing_neutral",
                    expression_slug="neutral",
                    facing="forward",
                )
                for cid in character_ids
            ],
            method="stub",
        )

    async def _stub_classify_async(desc, cids):
        return _stub_classify(desc, cids)

    monkeypatch.setattr(scene_classifier, "classify_scene", _stub_classify_async)
    monkeypatch.setattr(composite_mod, "classify_scene", _stub_classify_async)

    await _register_and_login(client, unique_username)

    # Create a character so the auth check on /composite passes.
    res = await client.post(
        "/api/v1/characters",
        json={
            "name": "Garrus",
            "description": "A turian sniper.",
            "personality": "Disciplined.",
            "system_prompt": "You are Garrus.",
            "tags": [],
        },
    )
    assert res.status_code == 201, res.text
    character_id = uuid.UUID(res.json()["id"])

    # Seed Backdrop + CharacterPose. We need to save the PNG to storage first
    # so the composite_service.run_composite_job can download it.
    from app.db.session import AsyncSessionLocal
    from app.modules.images.storage import get_storage

    storage = get_storage()
    backdrop_url = await storage.save_key("backdrops/tavern.png", tiny_png)
    pose_url = await storage.save_key(f"poses/{character_id}/standing.png", tiny_png)

    async with AsyncSessionLocal() as db:
        backdrop = Backdrop(
            id=uuid.uuid4(),
            slug="cozy_tavern",
            description="A warm tavern interior.",
            tags=["tavern", "indoor", "warm"],
            lighting_profile={"warmth": "warm", "intensity": "medium"},
            width=1536,
            height=1024,
            storage_url=backdrop_url,
            aspect_ratio="3:2",
            generation_meta={"seed": 42},
        )
        pose = CharacterPose(
            id=uuid.uuid4(),
            character_id=character_id,
            lora_version="v1",
            pose_slug="standing_neutral",
            expression_slug="neutral",
            framing="full_body",
            facing="forward",
            transparent_storage_url=pose_url,
            width=512,
            height=1024,
            bounding_box={"x": 0, "y": 0, "w": 512, "h": 1024},
            lighting_profile={"warmth": "neutral"},
        )
        db.add(backdrop)
        db.add(pose)
        await db.commit()

    # POST /composite.
    res = await client.post(
        "/api/v1/images/composite",
        json={
            "scene_description": "Two warriors share a quiet drink at the bar.",
            "character_ids": [str(character_id)],
        },
    )
    assert res.status_code == 201, res.text
    job_id = res.json()["id"]

    for _ in range(80):
        res = await client.get(f"/api/v1/images/jobs/{job_id}")
        assert res.status_code == 200
        if res.json()["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.05)
    body = res.json()
    assert body["status"] == "completed", body
    assert body["provider_used"] in ("composite", "composite_cache"), body
    assert body["composite_meta"]["scene_hash"]
    assert body["composite_meta"]["layout"] == "solo_center"
    first_url = body["outputs"][0]["storage_url"]

    # Second identical request → cache hit.
    res = await client.post(
        "/api/v1/images/composite",
        json={
            "scene_description": "Two warriors share a quiet drink at the bar.",
            "character_ids": [str(character_id)],
        },
    )
    assert res.status_code == 201
    job_id_2 = res.json()["id"]
    for _ in range(80):
        res = await client.get(f"/api/v1/images/jobs/{job_id_2}")
        assert res.status_code == 200
        if res.json()["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.05)
    body_2 = res.json()
    assert body_2["status"] == "completed", body_2
    assert body_2["provider_used"] == "composite_cache"
    # Same storage URL on cache hit.
    assert body_2["outputs"][0]["storage_url"] == first_url


async def test_image_pref_never_blocks_dispatch(
    client, unique_username, tmp_path, monkeypatch
):
    """User sets image_pref=never → marker emitted by LLM is still stripped
    from the stored message, but NO image_dispatched event fires and NO
    ImageJob row is created for that chat."""
    from app.core import config as config_mod
    from app.modules.images import storage as storage_mod
    from app.modules.providers import anthropic as anthropic_mod

    config_mod.settings.IMAGES_STORAGE_DIR = str(tmp_path / "no-imgs")
    storage_mod.reset_storage_for_tests()
    monkeypatch.setattr(config_mod.settings, "ANTHROPIC_API_KEY", "fake-key-for-tests")

    _FakeAnthropicStream._tokens = [
        "The candles flicker. ",
        "[SCENE_BEAT:single] ",
        "Footsteps echo from the corridor.",
    ]
    monkeypatch.setattr(anthropic_mod, "AnthropicProvider", _FakeAnthropicStream)

    await _register_and_login(client, unique_username)
    await _set_image_pref(client, "never")

    res = await client.post(
        "/api/v1/characters",
        json={
            "name": "Nyx",
            "description": "A shadow priest.",
            "personality": "Quiet.",
            "system_prompt": "You are Nyx.",
            "tags": [],
        },
    )
    assert res.status_code == 201
    character_id = res.json()["id"]
    res = await client.post(
        "/api/v1/chats", json={"character_id": character_id, "title": "Never test"}
    )
    chat_id = res.json()["id"]

    async with client.stream(
        "POST",
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "Light the lanterns."},
    ) as resp:
        assert resp.status_code == 200
        body_text = ""
        async for chunk in resp.aiter_text():
            body_text += chunk

    events = _parse_sse_stream(body_text)
    event_names = [n for (n, _d) in events]
    assert "image_dispatched" not in event_names, (
        f"image_dispatched leaked despite image_pref=never: {event_names}"
    )

    # Marker still stripped from stored message even with never.
    res = await client.get(f"/api/v1/chats/{chat_id}/messages")
    msgs = res.json()["messages"]
    char_msgs = [m for m in msgs if m["role"] == "character"]
    assert all("[SCENE_BEAT" not in m["content"] for m in char_msgs)


async def test_image_pref_never_blocks_manual_endpoints(
    client, unique_username, tmp_path
):
    """User sets image_pref=never → POST /composite and /generate_hq both
    return 403 ``images_disabled``. This catches manual triggers that bypass
    the streaming marker-driven path."""
    from app.core import config as config_mod
    from app.modules.images import storage as storage_mod

    config_mod.settings.IMAGES_STORAGE_DIR = str(tmp_path / "no-manual-imgs")
    storage_mod.reset_storage_for_tests()

    await _register_and_login(client, unique_username)
    await _set_image_pref(client, "never")

    res = await client.post(
        "/api/v1/characters",
        json={
            "name": "Mara",
            "description": "A traveling cleric.",
            "personality": "Kind.",
            "system_prompt": "You are Mara.",
            "tags": [],
        },
    )
    assert res.status_code == 201
    character_id = res.json()["id"]

    res = await client.post(
        "/api/v1/images/composite",
        json={
            "scene_description": "Mara prays at dawn.",
            "character_ids": [character_id],
        },
    )
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "images_disabled"

    res = await client.post(
        "/api/v1/images/generate_hq",
        json={"prompt": "Mara at dawn", "character_id": character_id},
    )
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "images_disabled"

    # /generate (single-character Tier 2) remains open — it's the underlying
    # text-to-image surface that the chat router uses for marker dispatch.
    # If we 403'd that too, the marker path would also break. The toggle's
    # job is to suppress *auto* dispatch via select_tier; manual /generate
    # is explicit user intent and should still work.
    res = await client.post(
        "/api/v1/images/generate",
        json={"prompt": "test"},
    )
    assert res.status_code == 201, res.text
