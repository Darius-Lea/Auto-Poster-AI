"""Stamp a city logo onto an image: large, bottom-right, on a dark rounded backing."""
from __future__ import annotations

import logging
import os
from typing import Optional

from PIL import Image, ImageDraw

log = logging.getLogger("core.watermark")

LOGO_WIDTH_FRAC = float(os.getenv("LOGO_WIDTH_FRAC", "0.45"))  # logo width as a share of photo width
MARGIN_FRAC = 0.025        # gap from the photo edges, as a share of photo width
PANEL_PAD_FRAC = 0.02      # padding inside the dark backing
PANEL_ALPHA = 165          # 0 = invisible backing, 255 = solid black


def _trim(logo: Image.Image) -> Image.Image:
    """Cut away fully transparent margins so the logo can be sized by its visible part."""
    box = logo.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    return logo.crop(box) if box else logo


def stamp(image_path: str, logo_path: str, out_path: str,
          position: str = "bottom_right") -> Optional[str]:
    """Return out_path on success, None (and log) on missing/corrupt input."""
    if not image_path or not os.path.isfile(image_path):
        log.error("Base image missing: %s", image_path)
        return None
    if not logo_path or not os.path.isfile(logo_path):
        log.error("Logo missing: %s", logo_path)
        return None
    try:
        base = Image.open(image_path).convert("RGBA")
        logo = _trim(Image.open(logo_path).convert("RGBA"))
    except Exception as exc:
        log.error("Could not open image/logo: %s", exc)
        return None
    try:
        bw, bh = base.size
        margin = max(6, int(bw * MARGIN_FRAC))
        pad = max(6, int(bw * PANEL_PAD_FRAC))

        new_w = max(1, int(bw * LOGO_WIDTH_FRAC))
        new_h = max(1, int(logo.height * new_w / logo.width))
        # never let the logo + backing grow past ~40% of the photo height
        max_h = int(bh * 0.40) - 2 * pad
        if new_h > max_h > 0:
            new_w = max(1, int(new_w * max_h / new_h))
            new_h = max_h
        logo = logo.resize((new_w, new_h), Image.LANCZOS)

        panel_w, panel_h = new_w + 2 * pad, new_h + 2 * pad
        if position == "bottom_left":
            px = margin
        elif position == "bottom_center":
            px = (bw - panel_w) // 2
        else:
            px = bw - panel_w - margin
        py = bh - panel_h - margin
        px, py = max(0, px), max(0, py)

        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rounded_rectangle(
            [px, py, px + panel_w, py + panel_h], radius=pad, fill=(0, 0, 0, PANEL_ALPHA))
        base = Image.alpha_composite(base, overlay)
        base.alpha_composite(logo, (px + pad, py + pad))

        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        base.convert("RGB").save(out_path, "JPEG", quality=95)
        return out_path
    except Exception as exc:
        log.error("Watermark failed: %s", exc)
        return None
