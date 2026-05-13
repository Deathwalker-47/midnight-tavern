"""Phase 5 HQ endpoint test.

Two flows are exercised:

1. Free-form prompt with no character_id → falls back to single-shot ``run_job``
   (the HQ pipeline can't inpaint without a character LoRA to anchor).
2. Prompt with a single character → real ``MultiCharPipeline`` runs Pass 0
   (backdrop) + Pass 1 (character inpaint). The DummyProvider's no-op
   inpaint is counted to assert the pipeline issued the expected number of
   passes; SSE event sequence is validated end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import re


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


async def _create_character(client) -> str:
    res = await client.post(
        "/api/v1/characters",
        json={
            "name": "Captain Vega",
            "description": "A weathered starship captain with steel-gray hair.",
            "personality": "Stoic, decisive.",
            "system_prompt": "You are Captain Vega.",
            "tags": ["sci-fi"],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _poll_until_terminal(client, job_id: str, attempts: int = 80) -> dict:
    for _ in range(attempts):
        res = await client.get(f"/api/v1/images/jobs/{job_id}")
        assert res.status_code == 200
        body = res.json()
        if body["status"] in ("completed", "failed", "canceled"):
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach a terminal state")


async def test_hq_endpoint_no_character_falls_back(client, unique_username, tmp_path):
    """No character → HQ delegates to ``run_job`` (single-shot). Contract
    preserved: response is ``job_kind = hq``, status reaches completed, an
    output URL is recorded."""
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

    body = await _poll_until_terminal(client, job_id)
    assert body["status"] == "completed", body
    assert body["job_kind"] == "hq"
    assert body["outputs"], "expected at least one HQ output"


async def test_hq_endpoint_with_character_runs_multicharpipeline(
    client, unique_username, tmp_path, monkeypatch
):
    """Single character → real MultiCharPipeline. Expected pass count: 2
    (Pass 0 backdrop + Pass 1 inpaint, no harmonization for 1 char)."""
    from app.core import config as config_mod
    from app.modules.images import storage as storage_mod
    from app.modules.images.providers import dummy as dummy_mod

    config_mod.settings.IMAGES_STORAGE_DIR = str(tmp_path / "hq-imgs-char")
    storage_mod.reset_storage_for_tests()

    # Count inpaint passes by wrapping DummyProvider.generate_inpaint.
    inpaint_calls: list[dict] = []
    original_inpaint = dummy_mod.DummyProvider.generate_inpaint

    async def _counting_inpaint(self, **kwargs):  # type: ignore[no-untyped-def]
        inpaint_calls.append({"strength": kwargs.get("strength"), "steps": kwargs.get("steps")})
        return await original_inpaint(self, **kwargs)

    monkeypatch.setattr(dummy_mod.DummyProvider, "generate_inpaint", _counting_inpaint)

    await _register_and_login(client, unique_username)
    character_id = await _create_character(client)

    # Subscribe to SSE BEFORE creating the job so we don't miss the early
    # events. The endpoint replays history, but a small race window can
    # still skip the queued event when polling. Subscribing first is safest.
    res = await client.post(
        "/api/v1/images/generate_hq",
        json={
            "prompt": "captain on the bridge of a starship, dramatic lighting",
            "character_id": character_id,
        },
    )
    assert res.status_code == 201, res.text
    job_id = res.json()["id"]

    # Poll the SSE stream for the event sequence. SSE frames are pairs:
    # ``event: <name>\ndata: <json>\n\n``. We capture the pending event_name
    # then attach the next data line.
    events: list[tuple[str, dict]] = []
    pending_event: str | None = None
    async with client.stream(
        "GET", f"/api/v1/images/jobs/{job_id}/events"
    ) as stream:
        async for chunk in stream.aiter_lines():
            if not chunk:
                continue
            m = re.match(r"^event: (.+)$", chunk)
            if m:
                pending_event = m.group(1)
                continue
            m = re.match(r"^data: (.+)$", chunk)
            if m and pending_event is not None:
                events.append((pending_event, json.loads(m.group(1))))
                if pending_event in ("image_completed", "image_failed", "image_canceled"):
                    break
                pending_event = None

    body = await _poll_until_terminal(client, job_id)
    assert body["status"] == "completed", body
    assert body["job_kind"] == "hq"
    assert body["provider_used"] == "hq_multichar", body
    assert body["outputs"], "HQ output missing"
    # composite_meta should record layout + canvas + num_characters.
    meta = body["composite_meta"]
    assert meta is not None
    assert meta["num_characters"] == 1
    assert meta["harmonized"] is False  # < 3 chars
    assert meta["layout"] == "solo_center"

    # Exactly one inpaint pass for a single character (no harmonize for n<3).
    assert len(inpaint_calls) == 1, f"expected 1 inpaint pass, got {len(inpaint_calls)}"

    # SSE sequence must include image_started, image_progress for backdrop +
    # character_1, then image_completed.
    event_names = [name for (name, _data) in events]
    assert "image_started" in event_names
    assert "image_completed" in event_names
    progress_stages = [
        d.get("stage") for (n, d) in events if n == "image_progress"
    ]
    assert "backdrop" in progress_stages
    assert "character_1" in progress_stages


async def test_hq_endpoint_honors_requested_dimensions(
    client, unique_username, tmp_path, monkeypatch
):
    """Codex P2: HQ must use the request's width/height instead of
    hardcoding a canvas. Verify by passing non-default dimensions and
    asserting composite_meta.canvas matches."""
    from app.core import config as config_mod
    from app.modules.images import storage as storage_mod
    from app.modules.images.providers import dummy as dummy_mod

    config_mod.settings.IMAGES_STORAGE_DIR = str(tmp_path / "hq-imgs-dims")
    storage_mod.reset_storage_for_tests()

    received_params: list[dict] = []
    original_inpaint = dummy_mod.DummyProvider.generate_inpaint

    async def _capture_inpaint(self, **kwargs):  # type: ignore[no-untyped-def]
        received_params.append({"width": kwargs["params"].get("width"), "height": kwargs["params"].get("height")})
        return await original_inpaint(self, **kwargs)

    monkeypatch.setattr(dummy_mod.DummyProvider, "generate_inpaint", _capture_inpaint)

    await _register_and_login(client, unique_username)
    character_id = await _create_character(client)

    res = await client.post(
        "/api/v1/images/generate_hq",
        json={
            "prompt": "wide panoramic vista",
            "character_id": character_id,
            "width": 1536,
            "height": 1024,
        },
    )
    assert res.status_code == 201, res.text
    job_id = res.json()["id"]

    body = await _poll_until_terminal(client, job_id)
    assert body["status"] == "completed", body
    assert body["composite_meta"]["canvas"] == [1536, 1024]
    # The output dimensions match.
    assert body["outputs"][0]["width"] == 1536
    assert body["outputs"][0]["height"] == 1024
    # The inpaint pass got the requested canvas, not a hardcoded default.
    assert received_params == [{"width": 1536, "height": 1024}]


async def test_hq_endpoint_fails_when_no_inpaint_provider_succeeds(
    client, unique_username, tmp_path, monkeypatch
):
    """Codex P1: when every inpaint provider fails, the job must be
    marked failed (with an error_code) instead of completing silently with
    only the backdrop bytes."""
    from app.core import config as config_mod
    from app.modules.images import storage as storage_mod
    from app.modules.images.providers import dummy as dummy_mod

    config_mod.settings.IMAGES_STORAGE_DIR = str(tmp_path / "hq-imgs-fail")
    storage_mod.reset_storage_for_tests()

    async def _always_fail_inpaint(self, **_kwargs):  # type: ignore[no-untyped-def]
        # Simulate a provider that doesn't implement inpaint at all (the
        # fal/wavespeed case Codex flagged).
        raise NotImplementedError("dummy inpaint disabled for this test")

    monkeypatch.setattr(dummy_mod.DummyProvider, "generate_inpaint", _always_fail_inpaint)

    await _register_and_login(client, unique_username)
    character_id = await _create_character(client)

    res = await client.post(
        "/api/v1/images/generate_hq",
        json={"prompt": "captain on bridge", "character_id": character_id},
    )
    assert res.status_code == 201, res.text
    job_id = res.json()["id"]

    body = await _poll_until_terminal(client, job_id)
    assert body["status"] == "failed", body
    assert body["error_code"] == "hq_no_inpaint_provider"


async def test_hq_endpoint_requires_auth(client) -> None:
    res = await client.post(
        "/api/v1/images/generate_hq", json={"prompt": "anonymous attempt"}
    )
    assert res.status_code == 401
