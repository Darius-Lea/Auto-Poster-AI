"""Stamp a city logo onto an image with a semi-transparent band."""
from __future__ import annotations

import logging
import os
from typing import Optional

from PIL import Image, ImageDraw

log = logging.getLogger("core.watermark")

PADDING = 20
BAND_ALPHA = 140


def stamp(image_path: str, logo_path: str, out_path: str,
          position: str = "bottom_center") -> Optional[str]:
    """Return out_path on success, None (and log) on missing/corrupt input."""
    if not image_path or not os.path.isfile(image_path):
        log.error("Base image missing: %s", image_path)
        return None
    if not logo_path or not os.path.isfile(logo_path):
        log.error("Logo missing: %s", logo_path)
        return None
    try:
        base = Image.open(image_path).convert("RGBA")
        logo = Image.open(logo_path).convert("RGBA")
    except Exception as exc:
        log.error("Could not open image/logo: %s", exc)
        return None
    try:
        bw, bh = base.size
        new_w = max(1, int(bw * 0.15))
        new_h = max(1, int(logo.height * new_w / logo.width))
        logo = logo.resize((new_w, new_h), Image.LANCZOS)

        band_h = min(bh, new_h + PADDING * 2)
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle([0, bh - band_h, bw, bh], fill=(0, 0, 0, BAND_ALPHA))
        base = Image.alpha_composite(base, overlay)

        y = bh - band_h + (band_h - new_h) // 2
        if position == "bottom_right":
            x = bw - new_w - PADDING
        else:
            x = (bw - new_w) // 2
        base.alpha_composite(logo, (max(0, x), max(0, y)))

        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        base.convert("RGB").save(out_path, "JPEG", quality=95)
        return out_path
    except Exception as exc:
        log.error("Watermark failed: %s", exc)
        return None
