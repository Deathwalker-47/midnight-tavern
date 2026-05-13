"""Tests for the PIL composer.

Hermetic — uses programmatically-generated PIL images, no fixtures on disk.
Covers: 1/2/5 character layouts produce expected dimensions; 6+ rejected;
``scene_hash`` is stable + order-insensitive on pose ids.
"""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image

from app.modules.images.composer import (
    MAX_CHARACTERS,
    CompositionError,
    PoseSlot,
    compose_scene,
    pick_layout,
    scene_hash,
)


def _png_bytes(width: int, height: int, color: tuple[int, int, int, int]) -> bytes:
    img = Image.new("RGBA", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _backdrop() -> bytes:
    return _png_bytes(1536, 1024, (50, 50, 80, 255))


def _pose() -> bytes:
    return _png_bytes(600, 1000, (220, 180, 160, 255))


def test_layout_template_picker():
    assert pick_layout(1)[0] == "solo_center"
    assert pick_layout(2)[0] == "side_by_side"
    assert pick_layout(3)[0] == "triangle"
    assert pick_layout(5)[0] == "lineup_5"


def test_layout_picker_rejects_zero():
    with pytest.raises(CompositionError):
        pick_layout(0)


def test_layout_picker_rejects_over_max():
    with pytest.raises(CompositionError):
        pick_layout(MAX_CHARACTERS + 1)


@pytest.mark.parametrize("num_chars", [1, 2, 3, 4, 5])
def test_compose_scene_produces_valid_png_for_each_count(num_chars):
    poses = [PoseSlot(pose_bytes=_pose(), bounding_box=None) for _ in range(num_chars)]
    out = compose_scene(_backdrop(), poses)
    img = Image.open(io.BytesIO(out))
    assert img.format == "PNG"
    assert img.size == (1536, 1024)


def test_compose_scene_resizes_to_target_dimensions():
    poses = [PoseSlot(pose_bytes=_pose(), bounding_box=None) for _ in range(2)]
    out = compose_scene(_backdrop(), poses, target_width=1024, target_height=768)
    img = Image.open(io.BytesIO(out))
    assert img.size == (1024, 768)


def test_compose_scene_requires_at_least_one_pose():
    with pytest.raises(CompositionError):
        compose_scene(_backdrop(), [])


def test_compose_scene_respects_bounding_box_crop():
    """A pose with a tight bbox should still composite without errors."""
    pose = PoseSlot(
        pose_bytes=_pose(),
        bounding_box={"x": 50, "y": 100, "w": 500, "h": 800},
    )
    out = compose_scene(_backdrop(), [pose])
    assert Image.open(io.BytesIO(out)).format == "PNG"


def test_scene_hash_stable_and_order_insensitive():
    bd = uuid.uuid4()
    p1, p2, p3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    a = scene_hash(bd, [p1, p2, p3], "triangle")
    b = scene_hash(bd, [p3, p1, p2], "triangle")
    assert a == b
    # Differ on layout.
    assert scene_hash(bd, [p1, p2, p3], "lineup_5") != a
    # Differ on backdrop.
    assert scene_hash(uuid.uuid4(), [p1, p2, p3], "triangle") != a
