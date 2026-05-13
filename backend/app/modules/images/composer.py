"""PIL composition logic for Tier 3 scene composites.

The composer is pure: it takes a backdrop image + N character poses
(transparent PNGs) and returns a composite. Layout is selected by character
count (2-5 chars supported; 6+ would degrade and is rejected upstream).

Each layout template defines a slot per character — ``(x, y, w, h)`` in
backdrop pixel coordinates. Poses are resized to fit their slot while
preserving aspect ratio (using the pre-computed ``bounding_box`` from
pose generation when available), then alpha-blended back-to-front.

The unification img2img pass is invoked separately by the orchestrator
in ``composite_service.run_composite_job``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from PIL import Image


@dataclass
class PoseSlot:
    pose_bytes: bytes
    bounding_box: dict[str, int] | None  # {x, y, w, h} of subject within pose image
    z_order: int = 0


# Layout templates: (backdrop_width, backdrop_height) → list of slot
# rectangles (x, y, w, h) in those coordinates. ``z_order`` is implied by list
# order — earlier = drawn first = behind.
#
# Designed for 3:2 backdrops at 1536×1024. Slots leave the upper third of the
# image untouched so the backdrop's sky/ceiling reads clearly.
LAYOUT_TEMPLATES: dict[int, list[tuple[int, int, int, int]]] = {
    1: [(420, 120, 700, 880)],
    2: [(200, 140, 600, 840), (760, 140, 600, 840)],
    3: [(100, 180, 480, 780), (540, 120, 460, 880), (960, 180, 480, 780)],
    4: [
        (60, 200, 380, 760),
        (440, 140, 380, 840),
        (820, 140, 380, 840),
        (1200, 200, 320, 760),
    ],
    5: [
        (30, 240, 320, 720),
        (350, 180, 320, 800),
        (660, 140, 360, 840),
        (1020, 180, 320, 800),
        (1340, 240, 200, 720),
    ],
}

LAYOUT_NAMES: dict[int, str] = {
    1: "solo_center",
    2: "side_by_side",
    3: "triangle",
    4: "lineup_4",
    5: "lineup_5",
}

MAX_CHARACTERS = 5


class CompositionError(Exception):
    pass


def pick_layout(num_characters: int) -> tuple[str, list[tuple[int, int, int, int]]]:
    if num_characters < 1:
        raise CompositionError("compose_scene requires at least 1 character")
    if num_characters > MAX_CHARACTERS:
        raise CompositionError(
            f"Tier 3 caps at {MAX_CHARACTERS} characters; got {num_characters}"
        )
    return LAYOUT_NAMES[num_characters], LAYOUT_TEMPLATES[num_characters]


def _crop_to_bbox(img: Image.Image, bbox: dict[str, int] | None) -> Image.Image:
    if not bbox:
        return img
    x, y, w, h = int(bbox["x"]), int(bbox["y"]), int(bbox["w"]), int(bbox["h"])
    if w <= 0 or h <= 0:
        return img
    x2 = min(img.width, x + w)
    y2 = min(img.height, y + h)
    return img.crop((max(0, x), max(0, y), x2, y2))


def _fit_into_slot(pose: Image.Image, slot_w: int, slot_h: int) -> Image.Image:
    """Resize a pose to fit a slot rectangle while preserving aspect ratio."""
    src_w, src_h = pose.size
    if src_w == 0 or src_h == 0:
        return pose
    scale = min(slot_w / src_w, slot_h / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    return pose.resize((new_w, new_h), Image.LANCZOS)


def compose_scene(
    backdrop_bytes: bytes,
    poses: list[PoseSlot],
    *,
    target_width: int | None = None,
    target_height: int | None = None,
) -> bytes:
    """Compose backdrop + poses into a single PNG.

    The poses are placed back-to-front (list order = z-order). Each pose is
    cropped to its bounding box (if provided), scaled to fit its layout slot
    while preserving aspect ratio, then alpha-blended onto the backdrop.
    """
    if not poses:
        raise CompositionError("compose_scene requires at least one pose")

    backdrop = Image.open(io.BytesIO(backdrop_bytes)).convert("RGBA")
    if target_width and target_height:
        backdrop = backdrop.resize((target_width, target_height), Image.LANCZOS)

    canvas_w, canvas_h = backdrop.size
    # Scale layout slots from the reference 1536×1024 to the actual canvas.
    ref_w, ref_h = 1536, 1024
    sx, sy = canvas_w / ref_w, canvas_h / ref_h

    _, slots_raw = pick_layout(len(poses))
    slots = [
        (int(x * sx), int(y * sy), int(w * sx), int(h * sy))
        for (x, y, w, h) in slots_raw
    ]

    composite = backdrop.copy()
    for pose_slot, slot in zip(poses, slots, strict=True):
        pose_img = Image.open(io.BytesIO(pose_slot.pose_bytes)).convert("RGBA")
        pose_img = _crop_to_bbox(pose_img, pose_slot.bounding_box)
        slot_x, slot_y, slot_w, slot_h = slot
        fitted = _fit_into_slot(pose_img, slot_w, slot_h)
        # Center-align within the slot.
        cx = slot_x + (slot_w - fitted.width) // 2
        cy = slot_y + (slot_h - fitted.height) // 2
        composite.alpha_composite(fitted, (cx, cy))

    buf = io.BytesIO()
    composite.convert("RGB").save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def scene_hash(
    backdrop_id: Any,
    pose_ids: list[Any],
    layout_template: str,
) -> str:
    """Stable hash for ``scene_composites`` cache lookups."""
    import hashlib

    sorted_ids = sorted(str(pid) for pid in pose_ids)
    payload = f"{backdrop_id}|{','.join(sorted_ids)}|{layout_template}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
