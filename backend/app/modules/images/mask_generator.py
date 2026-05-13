"""Inpainting mask generation for the HQ MultiCharPipeline (Phase 5).

Ported from Silly-Tavern-Flux-Bridge/flux_lora_bridge.py ``MaskGenerator``
(~lines 1700-1800). The upstream uses fractional center/halfsize slot dicts;
this port accepts pixel-space ``(x, y, w, h)`` rectangles directly so it can
reuse the same ``LAYOUT_TEMPLATES`` defined in ``composer.py`` without a
coordinate conversion layer.

White-on-black masks are returned as PNG bytes. The white region marks the
area to be re-painted; the surrounding black region is preserved by the
provider's inpaint endpoint. A Gaussian blur is applied to feather the edge
so the transition between original backdrop and the freshly-inpainted
character is gradual.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter

Rect = tuple[int, int, int, int]


def generate_slot_mask(
    canvas_width: int,
    canvas_height: int,
    slot: Rect,
    *,
    feather_px: int = 40,
    padding_pct: float = 0.05,
) -> bytes:
    """Build a feathered rectangular mask for a single character slot.

    The slot rectangle is expanded by ``padding_pct`` of the canvas in each
    direction before the Gaussian feather is applied, so the inpaint region
    extends slightly beyond the layout slot. This avoids hard seams where the
    character meets the preserved backdrop.
    """
    mask = Image.new("L", (canvas_width, canvas_height), 0)
    draw = ImageDraw.Draw(mask)

    sx, sy, sw, sh = slot
    pad_w = int(canvas_width * padding_pct)
    pad_h = int(canvas_height * padding_pct)
    x0 = max(0, sx - pad_w)
    y0 = max(0, sy - pad_h)
    x1 = min(canvas_width, sx + sw + pad_w)
    y1 = min(canvas_height, sy + sh + pad_h)

    draw.rectangle((x0, y0, x1, y1), fill=255)
    if feather_px > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather_px))

    buf = BytesIO()
    mask.save(buf, format="PNG")
    return buf.getvalue()


def generate_seam_mask(
    canvas_width: int,
    canvas_height: int,
    slots: list[Rect],
    *,
    seam_width_px: int = 120,
    blur_radius: int = 30,
) -> bytes:
    """Mask covering the vertical seams between adjacent slots, for the
    harmonization pass that runs once all characters are inpainted.

    The seam is centered between the x-centers of adjacent slots when ordered
    left-to-right and is a vertical strip of ``seam_width_px`` pixels.
    """
    mask = Image.new("L", (canvas_width, canvas_height), 0)
    draw = ImageDraw.Draw(mask)

    x_centers = sorted([sx + sw / 2 for (sx, _sy, sw, _sh) in slots])
    for i in range(len(x_centers) - 1):
        mid_x = (x_centers[i] + x_centers[i + 1]) / 2
        x0 = max(0, int(mid_x - seam_width_px / 2))
        x1 = min(canvas_width, int(mid_x + seam_width_px / 2))
        draw.rectangle((x0, 0, x1, canvas_height), fill=255)

    if blur_radius > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    buf = BytesIO()
    mask.save(buf, format="PNG")
    return buf.getvalue()
