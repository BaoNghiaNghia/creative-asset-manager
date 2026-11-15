"""Deterministic ColorMatch for C1717 SMS garment previews from verified PNGs.

Runs only during artwork ingestion (never in Picked list/modal requests).
No AI, network call, new preview files or pixels added to the original.
Recommendations are advisory. A new output SHA or algorithm version
invalidates previous rankings. Errors fail open to the existing palette.
"""
from __future__ import annotations

import colorsys
import io
import math
from collections import defaultdict
from PIL import Image, UnidentifiedImageError
from .color_match_palette import (
    ALGORITHM, GARMENT_COLORS, SALES_SPEED_PRIOR, SALES_PRIOR_SNAPSHOT,
)
from .color_match_readability import candidate_readability, analyze_ink_regions

_MAX_SAMPLES = 3600
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


def _balanced_ink_samples(pixels: list[tuple[int, int, int, int]]) -> tuple[list, dict[str, int]]:
    """Sample EACH ink family, so small lettering is not drowned by big art.

    The input is already bounded to <=768px in memory. No original is changed.
    Each family gets an equal capped budget; tiny groups are considered only
    if they account for meaningful nontransparent pixels.
    """
    buckets: dict[str, list[tuple[int, int, int, int]]] = defaultdict(list)
    for r, g, b, a in pixels:
        if a < 32:
            continue
        luminance = .21 * r + .72 * g + .07 * b
        if luminance < 68:
            family = "dark"
        elif luminance > 207:
            family = "light"
        elif max(r, g, b) - min(r, g, b) < 42:
            family = "neutral"
        else:
            family = f"hue-{int(colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[0] * 6) % 6}"
        buckets[family].append((r, g, b, a))
    if not buckets:
        return [], {}
    limit = max(80, _MAX_SAMPLES // len(buckets))
    sampled = []
    for family, group in sorted(buckets.items()):
        step = max(1, math.ceil(len(group) / limit))
        sampled.extend((_lab((r, g, b)), a / 255, family)
                       for r, g, b, a in group[::step])
    return sampled, {family: len(group) for family, group in buckets.items()}


def recommend_garment_colors(thumbnail_png: bytes, output_sha256: str, *,
                             analysis_png: bytes | None = None) -> dict | None:
    """Rank up to ten genuinely legible garment previews per design.

    No forced fallback colors: if every background risks hiding ink, return an
    empty shortlist and expose those colors only in the manual palette.
    Contrast across pigment families outranks both color diversity and sales.
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

    if not any(a >= 32 for _r, _g, _b, a in pixels):
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
    sampled, family_sizes = _balanced_ink_samples(analysis_pixels)
    if not sampled:
        return None
    meaningful_families = {name for name, count in family_sizes.items()
                           if count >= max(12, sum(family_sizes.values()) * .004)}
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
        family_signals: dict[str, list[float]] = defaultdict(list)
        hidden = 0.0
        weight_total = 0.0
        for ink, alpha, family in sampled:
            # Approximate alpha-composited contrast: transparent edges matter.
            l = bg_lab[0] * (1 - alpha) + ink[0] * alpha
            contrast = (max(l, bg_lab[0]) + .05) / (min(l, bg_lab[0]) + .05)
            perceptual = _distance(ink, bg_lab) * alpha
            # Log contrast avoids saturating every different garment at 100.
            score = (.58 * min(1, math.log(max(1.0, contrast)) / math.log(15))
                     + .42 * min(1, perceptual / .55))
            signals.append(score)
            family_signals[family].append(score)
            weight_total += alpha
            if score < .19:
                hidden += alpha
        signals.sort()
        low = signals[max(0, int(len(signals) * .20) - 1)]
        average = sum(signals) / len(signals)
        hidden_fraction = hidden / max(weight_total, .001)
        # The weakest meaningful ink family protects minority lettering/icons
        # that a full-image percentile would overlook.
        family_lows = []
        for family in meaningful_families:
            values = sorted(family_signals.get(family, []))
            if values:
                family_lows.append(values[max(0, int(len(values) * .20) - 1)])
        weakest_family = min(family_lows, default=low)
        chroma = math.hypot(bg_lab[2], bg_lab[3])
        neutral_bonus = max(0, 1 - chroma / .18)
        colorful_fraction = sum(count for family, count in family_sizes.items()
                                if family.startswith("hue-")) / max(1, sum(family_sizes.values()))
        # Subtle visual quietness preference on highly multicolored artwork.
        distraction = 5.0 * colorful_fraction * min(1, chroma / .18)
        rating = 100 * max(0, min(1,
            .34 * low + .25 * average + .30 * weakest_family
            + .11 * (1 - hidden_fraction) + .025 * neutral_bonus,
        )) - distraction
        rating = max(0, rating)
        # Sales speed is <1 point: it must never displace clearly better art contrast.
        popularity = SALES_SPEED_PRIOR.get(color_id, 0.0)
        combined = rating + .75 * popularity
        weighted.append((color_id, round(combined, 2), bg_lab, round(rating, 2)))
    weighted.sort(key=lambda row: (-row[1], -row[3], row[0]))
    unsafe_set = set(unsafe_colors)
    by_id = {row[0]: row for row in weighted}
    quality_candidates = [row for row in weighted if row[0] not in unsafe_set
                          and (not profiles or safety_margins[row[0]] >= 1.12)]
    strongest_ink_contrast = max((row[3] for row in quality_candidates), default=0)
    # Not a quota: requiring 78% of the best measured garment's readability
    # avoids including poor colors just to make ten. Do not diversify away
    # stronger adjacent neutrals or favor sales over image fidelity.
    # Art-only (no reliable text ROIs) can have softer luminance but strong
    # perceptual separation. Text-bearing work must clear a stricter floor.
    quality_floor = max(23.0 if profiles else 14.0, strongest_ink_contrast * .80)
    eligible_colors = [color for color, _combined, _lab_color, visual in quality_candidates
                       if visual >= quality_floor]
    best = eligible_colors[:10]
    review_colors: list[str] = []
    return {
        "version": ALGORITHM,
        "output_sha256": output_sha256,
        "recommended": best,
        "scores": {color: by_id[color][1] for color in best},
        "contrast_scores": {color: by_id[color][3] for color in best},
        "readability_margins": {color: safety_margins[color] for color in best},
        "unsafe_colors": unsafe_colors,
        "review_colors": review_colors,
        "readability_check": "region_ink_alpha_composite_v3",
        "method": "design_region_alpha_quality_first",
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
