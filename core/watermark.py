"""Stamp a city logo onto an image: large, bottom-right, crisp, with a soft shadow (no box)."""
from __future__ import annotations

import base64
import io
import logging
import os
from typing import Optional

from PIL import Image, ImageChops, ImageDraw, ImageFilter

log = logging.getLogger("core.watermark")

MIN_OUTPUT_WIDTH = 1080                                        # upscale small photos so the logo stays sharp
LOGO_WIDTH_FRAC = float(os.getenv("LOGO_WIDTH_FRAC", "0.45"))  # logo width as a share of photo width
MARGIN_FRAC = 0.025                                            # gap from the photo edges (share of width)


def _load_logo(logo_path: str) -> Image.Image:
    """Prefer a transparent copy stored as text (<name>.logo.txt, base64 PNG), because phones
    often flatten PNGs onto white when they are uploaded. Fall back to the image file itself."""
    txt = os.path.splitext(logo_path)[0] + ".logo.txt"
    if os.path.isfile(txt):
        try:
            with open(txt, "r", encoding="utf-8") as fh:
                raw = base64.b64decode("".join(fh.read().split()))
            return Image.open(io.BytesIO(raw)).convert("RGBA")
        except Exception as exc:
            log.warning("Could not read %s (%s); using %s", txt, exc, logo_path)
    return Image.open(logo_path).convert("RGBA")


def _clear_outer_white(logo: Image.Image) -> Image.Image:
    """Safety net for a logo with no transparency: remove white connected to the outer edge only,
    so light parts inside the artwork are left alone."""
    if logo.getchannel("A").getextrema()[0] < 250:
        return logo
    w, h = logo.size
    rgb = logo.convert("RGB")
    marker = (255, 0, 255)
    for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if min(rgb.getpixel(seed)) >= 240:
            ImageDraw.floodfill(rgb, seed, marker, thresh=18)
    mask = rgb.point(lambda v: 0)  # placeholder to keep mode
    r, g, b = rgb.split()
    is_marker = ImageChops.multiply(ImageChops.multiply(
        r.point(lambda v: 255 if v == 255 else 0),
        g.point(lambda v: 255 if v == 0 else 0)),
        b.point(lambda v: 255 if v == 255 else 0))
    alpha = ImageChops.subtract(logo.getchannel("A"), is_marker)
    out = logo.copy()
    out.putalpha(alpha)
    return out


def _trim(logo: Image.Image) -> Image.Image:
    box = logo.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    return logo.crop(box) if box else logo


def _shadow(logo: Image.Image, radius: int) -> Image.Image:
    """Dark halo shaped like the logo, so light letters stay readable on any photo."""
    pad = radius * 4
    canvas = Image.new("L", (logo.width + 2 * pad, logo.height + 2 * pad), 0)
    canvas.paste(logo.getchannel("A"), (pad, pad))
    grown = canvas.filter(ImageFilter.MaxFilter(2 * max(1, radius // 2) + 1))
    blurred = grown.filter(ImageFilter.GaussianBlur(radius))
    blurred = blurred.point(lambda v: min(255, int(v * 1.6)))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow.putalpha(blurred.point(lambda v: int(v * 0.85)))
    return shadow


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
        logo = _trim(_clear_outer_white(_load_logo(logo_path)))
    except Exception as exc:
        log.error("Could not open image/logo: %s", exc)
        return None
    try:
        if base.width < MIN_OUTPUT_WIDTH:
            scale = MIN_OUTPUT_WIDTH / base.width
            base = base.resize((MIN_OUTPUT_WIDTH, int(base.height * scale)), Image.LANCZOS)
        bw, bh = base.size
        margin = max(6, int(bw * MARGIN_FRAC))

        new_w = max(1, int(bw * LOGO_WIDTH_FRAC))
        new_h = max(1, int(logo.height * new_w / logo.width))
        max_h = int(bh * 0.30)                      # keep it from towering over short photos
        if new_h > max_h > 0:
            new_w = max(1, int(new_w * max_h / new_h))
            new_h = max_h
        logo = logo.resize((new_w, new_h), Image.LANCZOS)

        if position == "bottom_left":
            x = margin
        elif position == "bottom_center":
            x = (bw - new_w) // 2
        else:
            x = bw - new_w - margin
        y = bh - new_h - margin
        x, y = max(0, x), max(0, y)

        radius = max(3, new_w // 90)
        sh = _shadow(logo, radius)
        base.alpha_composite(sh, (max(0, x - radius * 4), max(0, y - radius * 4 + radius)))
        base.alpha_composite(logo, (x, y))

        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        base.convert("RGB").save(out_path, "JPEG", quality=95)
        return out_path
    except Exception as exc:
        log.error("Watermark failed: %s", exc)
        return None
