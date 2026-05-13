"""Unit tests for ``mask_generator`` (Phase 5).

Assertions are intentionally coarse — we verify the white region is roughly
where expected for each layout and that the seam mask covers transitions
between slot centers. Pixel-perfect comparisons would be brittle against the
Gaussian feather.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.modules.images.composer import LAYOUT_TEMPLATES, pick_layout
from app.modules.images.mask_generator import (
    generate_seam_mask,
    generate_slot_mask,
)


def _open_mask(data: bytes) -> Image.Image:
    return Image.open(BytesIO(data)).convert("L")


@pytest.mark.parametrize("num_chars", [1, 2, 3, 4, 5])
def test_slot_mask_covers_slot_region(num_chars: int) -> None:
    """For each layout, the slot mask must be bright inside the slot rect and
    near-dark in the upper-left corner (which is always empty by design)."""
    canvas_w, canvas_h = 1536, 1024
    _, slots = pick_layout(num_chars)

    for slot in slots:
        sx, sy, sw, sh = slot
        mask = _open_mask(generate_slot_mask(canvas_w, canvas_h, slot, feather_px=20))
        # Pixel near slot center should be high.
        center = mask.getpixel((sx + sw // 2, sy + sh // 2))
        assert center > 200, f"slot center should be white in {num_chars}-char layout"
        # Far corner (0, 0) should be dark (only foreground slots reach it, but
        # the upper-left corner is excluded from every layout).
        if sx > 100 and sy > 100:
            corner = mask.getpixel((0, 0))
            assert corner < 50, f"far corner should be dark for slot {slot}"


def test_slot_mask_respects_canvas_bounds() -> None:
    """Mask dimensions must match the canvas exactly."""
    canvas_w, canvas_h = 1024, 768
    mask = _open_mask(
        generate_slot_mask(canvas_w, canvas_h, (200, 100, 400, 500), feather_px=10)
    )
    assert mask.size == (canvas_w, canvas_h)


def test_seam_mask_covers_between_slots() -> None:
    """For a 3-character layout, seams between adjacent slot centers should
    be bright; the far-left and far-right edges should be dark."""
    canvas_w, canvas_h = 1536, 1024
    _, slots = pick_layout(3)
    mask = _open_mask(generate_seam_mask(canvas_w, canvas_h, slots, seam_width_px=100))

    # Compute expected seam x-coords from sorted slot centers.
    x_centers = sorted([sx + sw / 2 for (sx, _sy, sw, _sh) in slots])
    for i in range(len(x_centers) - 1):
        mid_x = int((x_centers[i] + x_centers[i + 1]) / 2)
        # Pick a y in the middle of the canvas.
        seam_pixel = mask.getpixel((mid_x, canvas_h // 2))
        assert seam_pixel > 100, f"seam at x={mid_x} should be bright"

    # Far-left edge should be dark (no seam covers it).
    assert mask.getpixel((0, canvas_h // 2)) < 50
    assert mask.getpixel((canvas_w - 1, canvas_h // 2)) < 50


def test_seam_mask_empty_for_single_slot() -> None:
    """A 1-slot layout has no seams — the entire mask should be uniformly dark."""
    mask = _open_mask(generate_seam_mask(1024, 1024, [(100, 100, 600, 600)]))
    # No bright pixels expected.
    bbox = mask.getbbox()
    # ``getbbox`` returns None if the image is entirely black or, with a tiny
    # Gaussian residual, a very small bounding box. Either is acceptable.
    if bbox is not None:
        x0, y0, x1, y1 = bbox
        assert (x1 - x0) * (y1 - y0) < 100, "seam mask for 1 slot should be empty"


def test_layout_templates_match_pick_layout() -> None:
    """Sanity: LAYOUT_TEMPLATES and pick_layout return the same slots."""
    for n in (1, 2, 3, 4, 5):
        _, slots = pick_layout(n)
        assert slots == LAYOUT_TEMPLATES[n]
