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
from .color_match_readability import candidate_readability, analyze_ink_regions

_MAX_SAMPLES = 2000
_MAX_THUMB_BYTES = 2 * 1024 * 1024
_MAX_ANALYSIS_BYTES = 6 * 1024 * 1024
_MAX_ANALYSIS_EDGE = 768


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


def recommend_garment_colors(thumbnail_png: bytes, output_sha256: str, *,
                             analysis_png: bytes | None = None) -> dict | None:
    """Rank exactly ten C1717 previews with risk labeled by design.

    Verified readable colors are always ranked first. If fewer than ten
    survive the readability gate, fill remaining slots by highest measured
    contrast margin, marking these extra colors as needing visual review.
    Such fallbacks are NOT labeled printable or safe.

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
            rgba = image.convert("RGBA")
            pixels = list(rgba.get_flattened_data() if hasattr(rgba, "get_flattened_data") else rgba.getdata())
            width, height = image.size
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None

    step = max(1, len(pixels) // _MAX_SAMPLES)
    sampled = []
    for r, g, b, a in pixels[::step]:
        if a < 105:
            continue
        sampled.append((_lab((r, g, b)), a / 255))
    if len(sampled) < 12:
        # Very small chest/sleeve marks can fall between 2,000 sparse
        # sampling points. Re-examine bounded native thumbnail pixels so
        # an actual but tiny design still receives automatic candidates.
        sampled = [(_lab((r, g, b)), a / 255) for r, g, b, a in pixels
                   if a >= 32][:_MAX_SAMPLES]
    if not sampled:
        # A fully transparent PNG has no artwork to score.
        return None

    analysis_pixels = pixels
    analysis_width, analysis_height = width, height
    if isinstance(analysis_png, bytes) and len(analysis_png) <= _MAX_ANALYSIS_BYTES:
        try:
            with Image.open(io.BytesIO(analysis_png)) as analysis:
                if (analysis.format == "PNG"
                        and 1 <= analysis.width <= _MAX_ANALYSIS_EDGE
                        and 1 <= analysis.height <= _MAX_ANALYSIS_EDGE):
                    analysis_width, analysis_height = analysis.size
                    medium = analysis.convert("RGBA")
                    analysis_pixels = list(medium.get_flattened_data() if hasattr(medium, "get_flattened_data") else medium.getdata())
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            pass
    profile = analyze_ink_regions(analysis_pixels, analysis_width, analysis_height)
    profiles = profile["profiles"]
    microtext_count = sum(
        r["kind"] == "probable_text" and r["bbox"][3] - r["bbox"][1] <= 90
        for r in profile["regions"]
    )
    # Re-check small-letter regions at medium resolution without re-encoding
    # master artwork or creating any new persistent file.
    if analysis_pixels is not pixels and microtext_count:
        source_image = Image.new("RGBA", (analysis_width, analysis_height))
        source_image.putdata(analysis_pixels)
        checked = 0
        for region in profile["regions"]:
            if (region["kind"] != "probable_text" or
                    region["bbox"][3] - region["bbox"][1] > 90):
                continue
            x0, y0, x1, y1 = region["bbox"]
            rect = (max(0, x0 * analysis_width // 1000 - 3),
                    max(0, y0 * analysis_height // 1000 - 3),
                    min(analysis_width, x1 * analysis_width // 1000 + 3),
                    min(analysis_height, y1 * analysis_height // 1000 + 3))
            crop = source_image.crop(rect)
            if crop.width > 5 and crop.height > 5:
                micro_profile = analyze_ink_regions(
                    list(crop.get_flattened_data() if hasattr(crop, "get_flattened_data") else crop.getdata()),
                    *crop.size)
                if micro_profile["profiles"]:
                    checked += 1
            if checked >= 6:
                break
        microtext_count = max(microtext_count, checked)
    weighted = []
    unsafe_colors: list[str] = []
    safety_margins: dict[str, float] = {}
    for color_id, hex_rgb in GARMENT_COLORS:
        bg = tuple(int(hex_rgb[i:i+2], 16) for i in (1, 3, 5))
        bg_lab = _lab(bg)
        # Never label unreadable artwork printable. Still retain its
        # contrast score for a bounded, explicitly flagged preview fallback.
        safe, margin = candidate_readability(profiles, bg)
        if not safe:
            unsafe_colors.append(color_id)
        safety_margins[color_id] = margin
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
    unsafe_set = set(unsafe_colors)
    strongest_ink_contrast = max(
        (row[3] for row in weighted if row[0] not in unsafe_set), default=0)
    quality_floor = max(21.0, strongest_ink_contrast * .56)
    readable = [row for row in weighted
                if row[0] not in unsafe_set and row[3] >= quality_floor]
    safe_ranked = [row for row in weighted if row[0] not in unsafe_set]
    by_id = {row[0]: row for row in weighted}
    best: list[str] = []
    # Prefer high-readability, diverse colors. Never use a flagged color
    # before exhausting the verified safe colors.
    for min_distance in (.090, .060, .035, .0):
        for color_id, _score, lab, _visual in readable:
            if color_id in best:
                continue
            if any(_distance(lab, by_id[chosen][2]) < min_distance
                   for chosen in best):
                continue
            best.append(color_id)
            if len(best) == 10:
                break
        if len(best) == 10:
            break
    for row in safe_ranked:
        if len(best) == 10:
            break
        if row[0] not in best:
            best.append(row[0])
    # Only when <10 safe colors exist, add lowest-risk preview candidates.
    # A hard-gated color stays in unsafe_colors, and the UI marks it clearly.
    if len(best) < 10:
        review_ranked = sorted(
            (row for row in weighted if row[0] in unsafe_set),
            key=lambda row: (-safety_margins[row[0]], -row[3], -row[1], row[0]),
        )
        for row in review_ranked:
            if len(best) == 10:
                break
            if row[0] not in best:
                best.append(row[0])
    secondary_floor = max(16.0, strongest_ink_contrast * .42)
    eligible_colors = [color for color, _combined, _lab_color, visual in weighted
                       if color not in unsafe_set and visual >= secondary_floor]
    eligible_set = set(eligible_colors)
    review_colors = [color for color in best if color not in eligible_set]
    return {
        "version": ALGORITHM,
        "output_sha256": output_sha256,
        "recommended": best,
        "scores": {color: by_id[color][1] for color in best},
        "contrast_scores": {color: by_id[color][3] for color in best},
        "readability_margins": {color: safety_margins[color] for color in best},
        "unsafe_colors": unsafe_colors,
        "review_colors": review_colors,
        "readability_check": "region_ink_alpha_composite_v2",
        "method": "design_region_alpha_sales_prior_ten",
        "eligible_colors": eligible_colors,
        "analysis": {
            "resolution": [analysis_width, analysis_height],
            "source": "medium" if analysis_pixels is not pixels else "thumbnail",
            "regions": profile["regions"][:12],
            "text_regions": profile["text_regions"],
            "microtext_regions": microtext_count,
            "faded_ink": profile["faded_ink"],
            "print_review_required": bool(profile["faded_ink"] or microtext_count
                                          or analysis_pixels is pixels),
        },
        "popularity_source": SALES_PRIOR_SNAPSHOT,
        "insufficient_safe_colors": len(eligible_colors) < 7,
    }
