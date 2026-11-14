"""Deterministic ColorMatch for C1717 SMS garment previews from the thumbnail.

Runs only during artwork ingestion (never in Picked list/modal requests).
No AI, network call, new preview files or pixels added to the original.
Recommendations are advisory. A new output SHA or algorithm version
invalidates previous rankings. Errors fail open to the existing palette.
"""
from __future__ import annotations

import io
import math
from PIL import Image, UnidentifiedImageError
from .color_match_palette import (
    ALGORITHM, GARMENT_COLORS, SALES_SPEED_PRIOR, SALES_PRIOR_SNAPSHOT,
)

_MAX_SAMPLES = 2000
_MAX_THUMB_BYTES = 2 * 1024 * 1024


def _linear(v: float) -> float:
    v /= 255
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _lab(rgb: tuple[int, int, int]) -> tuple[float, float, float, float]:
    r, g, b = (_linear(c) for c in rgb)
    luminance = .2126 * r + .7152 * g + .0722 * b
    l = (.4122214708 * r + .5363325363 * g + .0514459929 * b) ** (1/3)
    m = (.2119034982 * r + .6806995451 * g + .1073969566 * b) ** (1/3)
    s = (.0883024619 * r + .2817188376 * g + .6299787005 * b) ** (1/3)
    return (
        luminance,
        .2104542553 * l + .7936177850 * m - .0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + .4505937099 * s,
        .0259040371 * l + .7827717662 * m - .8086757660 * s,
    )


def _distance(a: tuple[float, float, float, float],
              b: tuple[float, float, float, float]) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in (1, 2, 3)))


def recommend_garment_colors(thumbnail_png: bytes, output_sha256: str) -> dict | None:
    """Rank 7-10 distinct C1717 colors by ink readability, then SMS demand.

    If fewer than seven colors pass the minimum safety guard, return only
    verified readable options instead of inventing visually unsafe matches.

    Samples only a capped 192px PNG that was already created at upload time.
    Small/high-opacity ink regions are not hidden by the dominant color:
    20th-percentile ink contrast and the fraction of low-contrast samples
    carry more weight than mean contrast. No full master decoding.
    """
    if (not isinstance(thumbnail_png, bytes) or len(thumbnail_png) > _MAX_THUMB_BYTES
            or not isinstance(output_sha256, str) or len(output_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in output_sha256)):
        return None
    try:
        with Image.open(io.BytesIO(thumbnail_png)) as image:
            if image.format != "PNG" or image.width > 256 or image.height > 256:
                return None
            pixels = list(image.convert("RGBA").getdata())
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None

    step = max(1, len(pixels) // _MAX_SAMPLES)
    sampled = []
    for r, g, b, a in pixels[::step]:
        if a < 105:
            continue
        sampled.append((_lab((r, g, b)), a / 255))
    if len(sampled) < 12:
        return None

    weighted = []
    for color_id, hex_rgb in GARMENT_COLORS:
        bg = tuple(int(hex_rgb[i:i+2], 16) for i in (1, 3, 5))
        bg_lab = _lab(bg)
        signals: list[float] = []
        hidden = 0.0
        weight_total = 0.0
        for ink, alpha in sampled:
            # Approximate alpha-composited contrast: transparent edges matter.
            l = bg_lab[0] * (1 - alpha) + ink[0] * alpha
            contrast = (max(l, bg_lab[0]) + .05) / (min(l, bg_lab[0]) + .05)
            perceptual = _distance(ink, bg_lab) * alpha
            # Log contrast avoids saturating every different garment at 100.
            score = (.58 * min(1, math.log(max(1.0, contrast)) / math.log(15))
                     + .42 * min(1, perceptual / .55))
            signals.append(score)
            weight_total += alpha
            if score < .19:
                hidden += alpha
        signals.sort()
        low = signals[max(0, int(len(signals) * .20) - 1)]
        average = sum(signals) / len(signals)
        hidden_fraction = hidden / max(weight_total, .001)
        # Favor neutral/quiet garment backgrounds only as a tiebreaker; the
        # strongest criterion remains actual foreground legibility.
        chroma = math.hypot(bg_lab[2], bg_lab[3])
        neutral_bonus = max(0, 1 - chroma / .18)
        rating = 100 * max(0, min(1,
            .49 * low + .36 * average + .15 * (1 - hidden_fraction)
            + .035 * neutral_bonus,
        ))
        # A sales prior only breaks ties among garments readable for this
        # design; it must not rescue an unreadable background.
        popularity = SALES_SPEED_PRIOR.get(color_id, 0.0)
        combined = .92 * rating + 8.0 * popularity
        weighted.append((color_id, round(combined, 1), bg_lab, round(rating, 1)))
    weighted.sort(key=lambda row: (-row[1], -row[3], row[0]))
    strongest_ink_contrast = max(row[3] for row in weighted)
    quality_floor = max(21.0, strongest_ink_contrast * .56)
    readable = [row for row in weighted if row[3] >= quality_floor]
    best: list[str] = []
    # Prefer garment colors that are perceptually varied. Relax duplication
    # only if needed to reach at least seven distinct readable choices.
    for min_distance in (.090, .060, .035, .0):
        for color_id, _score, lab, _visual in readable:
            if color_id in best:
                continue
            if any(_distance(lab, next(row[2] for row in weighted if row[0] == chosen))
                   < min_distance for chosen in best):
                continue
            best.append(color_id)
            if len(best) == 10:
                break
        if len(best) >= 7:
            break
    if len(best) < 7:
        # Do not force bad contrast to satisfy the count target.
        secondary_floor = max(16.0, strongest_ink_contrast * .42)
        for color_id, _score, _unused_lab, visual in weighted:
            if color_id not in best and visual >= secondary_floor:
                best.append(color_id)
                if len(best) >= 7:
                    break
    if not best:
        return None
    by_id = {row[0]: row for row in weighted}
    return {
        "version": ALGORITHM,
        "output_sha256": output_sha256,
        "recommended": best,
        "scores": {color: by_id[color][1] for color in best},
        "contrast_scores": {color: by_id[color][3] for color in best},
        "method": "thumbnail_pixel_contrast_sales_prior",
        "popularity_source": SALES_PRIOR_SNAPSHOT,
        "insufficient_safe_colors": len(best) < 7,
    }
