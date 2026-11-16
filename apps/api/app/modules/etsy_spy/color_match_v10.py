"""V10 local artwork-aware garment ranking (no network, models, or image edits).

Approximates text/illustration roles using transparent-ink geometry (not OCR).
Uses region saliency, alpha-composited readability, CIEDE2000 color distance,
and hue/chroma/tone harmony. A review preview is NEVER labeled print-safe.
All computations are bounded and deterministic for an immutable source SHA.
"""
from __future__ import annotations

import colorsys
import math
from collections import defaultdict
from typing import Any

from .color_match_palette import GARMENT_COLORS, SALES_SPEED_PRIOR, SALES_PRIOR_SNAPSHOT
from .color_match_readability import analyze_ink_regions

_MAX_GROUP_SAMPLES = 52
_SAMPLE_LIMIT = 3100
_DEG = math.pi / 180


def _linear(x: int) -> float:
    v = x / 255
    return v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4


def _luma(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_linear(n) for n in rgb)
    return .2126 * r + .7152 * g + .0722 * b


def _oklab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (_linear(n) for n in rgb)
    l = (.4122214708 * r + .5363325363 * g + .0514459929 * b) ** (1 / 3)
    m = (.2119034982 * r + .6806995451 * g + .1073969566 * b) ** (1 / 3)
    s = (.0883024619 * r + .2817188376 * g + .6299787005 * b) ** (1 / 3)
    return (.2104542553 * l + .7936177850 * m - .0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + .4505937099 * s,
            .0259040371 * l + .7827717662 * m - .8086757660 * s)


def _cielab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (_linear(n) for n in rgb)
    x = (.4124564 * r + .3575761 * g + .1804375 * b) / .95047
    y = .2126729 * r + .7151522 * g + .0721750 * b
    z = (.0193339 * r + .1191920 * g + .9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e_2000(c1: tuple[float, float, float],
                 c2: tuple[float, float, float]) -> float:
    """ISO/CIE 11664-6 CIEDE2000 (unit parametric weights, D65 sRGB->Lab)."""
    l1, a1, b1 = c1
    l2, a2, b2 = c2
    cab = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = .5 * (1 - math.sqrt(cab ** 7 / (cab ** 7 + 25 ** 7)))
    a1p, a2p = a1 * (1 + g), a2 * (1 + g)
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1 = (math.degrees(math.atan2(b1, a1p)) + 360) % 360 if c1p else 0
    h2 = (math.degrees(math.atan2(b2, a2p)) + 360) % 360 if c2p else 0
    dl = l2 - l1
    dc = c2p - c1p
    dh = 0 if c1p * c2p == 0 else h2 - h1
    if dh > 180:
        dh -= 360
    elif dh < -180:
        dh += 360
    dhp = 2 * math.sqrt(c1p * c2p) * math.sin(dh / 2 * _DEG)
    havg = h1 + h2 if c1p * c2p == 0 else (h1 + h2) / 2
    if c1p * c2p and abs(h1 - h2) > 180:
        havg += 180 if h1 + h2 < 360 else -180
    havg %= 360
    lavg = (l1 + l2) / 2
    cavg = (c1p + c2p) / 2
    t = (1 - .17 * math.cos((havg - 30) * _DEG)
         + .24 * math.cos(2 * havg * _DEG)
         + .32 * math.cos((3 * havg + 6) * _DEG)
         - .20 * math.cos((4 * havg - 63) * _DEG))
    theta = 30 * math.exp(-((havg - 275) / 25) ** 2)
    rc = 2 * math.sqrt(cavg ** 7 / (cavg ** 7 + 25 ** 7))
    sl = 1 + .015 * (lavg - 50) ** 2 / math.sqrt(20 + (lavg - 50) ** 2)
    sc = 1 + .045 * cavg
    sh = 1 + .015 * cavg * t
    rt = -math.sin(2 * theta * _DEG) * rc
    a, b, c = dl / sl, dc / sc, dhp / sh
    return math.sqrt(max(0, a * a + b * b + c * c + rt * b * c))


def _family(rgb: tuple[int, int, int], luma: float) -> str:
    r, g, b = rgb
    chroma = max(rgb) - min(rgb)
    if luma <= .15 and chroma <= 90:
        return "dark-ink"
    if luma >= .68 and chroma <= 90:
        return "light-ink"
    if chroma <= 28:
        return "mid-neutral"
    h, _s, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return f"chromatic-{round(h * 12) % 12}-{min(4, int(luma * 5))}"


def _contrast(a: float, b: float) -> float:
    return (max(a, b) + .05) / (min(a, b) + .05)


def _clamp(value: float, low: float = 0, high: float = 1) -> float:
    return min(high, max(low, value))


def _sample_ink(pixels: list[tuple[int, int, int, int]], width: int, height: int):
    """Deterministic bounded per-family samples with local edge saliency."""
    groups: dict[str, dict[str, Any]] = {}
    total = 0
    for index, (r, g, b, a) in enumerate(pixels):
        if a < 32:
            continue
        rgb = (r, g, b)
        pigment = _family(rgb, _luma(rgb))
        group = groups.setdefault(pigment, {"count": 0, "rgb": [0, 0, 0], "samples": []})
        group["count"] += 1
        total += 1
        for channel, value in enumerate(rgb):
            group["rgb"][channel] += value
        entries = group["samples"]
        if len(entries) < _MAX_GROUP_SAMPLES:
            entries.append((index, rgb, a))
        else:
            # Constant-memory deterministic reservoir, independent of hash salt.
            seen = group["count"]
            pick = ((index * 2654435761) ^ (seen * 2246822519)) % seen
            if pick < _MAX_GROUP_SAMPLES:
                entries[pick] = (index, rgb, a)

    families = {}
    for key, info in groups.items():
        samples = []
        for index, rgb, alpha in info["samples"]:
            x, y = index % width, index // width
            other_index = index + (1 if x + 1 < width else -1)
            other = pixels[other_index] if 0 <= other_index < len(pixels) else (0, 0, 0, 0)
            edge = (abs(alpha - other[3]) / 255 +
                    sum(abs(rgb[j] - other[j]) for j in range(3)) / 765)
            salience = .65 + .35 * _clamp(edge)
            samples.append((_luma(rgb), _oklab(rgb), alpha / 255, salience))
        families[key] = {"samples": samples, "count": info["count"],
                         "mean_rgb": tuple(round(n / info["count"]) for n in info["rgb"])}
    return families, total


def _ink_visibility(families: dict, total: int, bg: tuple[int, int, int]):
    bg_l = _luma(bg)
    bg_lab = _oklab(bg)
    values = []
    weights = []
    important: list[tuple[float, int]] = []
    for key, group in families.items():
        size = group["count"]
        if size < max(9, total * .002) or not group["samples"]:
            continue
        family_scores = []
        for luminance, lab, opacity, salience in group["samples"]:
            effective = opacity * luminance + (1 - opacity) * bg_l
            ratio = _contrast(effective, bg_l)
            lab_distance = math.sqrt(sum((lab[i] - bg_lab[i]) ** 2 for i in range(3))) * opacity
            visual = (.60 * _clamp(math.log(ratio) / math.log(7))
                      + .40 * _clamp(lab_distance / .48))
            family_scores.append(visual * salience)
        family_scores.sort()
        lowest = family_scores[max(0, int(len(family_scores) * .20) - 1)]
        visibility = .72 * sum(family_scores) / len(family_scores) + .28 * lowest
        weight = min(.28, math.sqrt(size / max(1, total))) * (
            1.08 if key.startswith("dark") or key.startswith("light") else 1)
        values.append(visibility)
        weights.append(weight)
        if size >= total * .04:
            important.append((visibility, size))
    if not weights:
        return 0.0, 0.0
    score = sum(x * w for x, w in zip(values, weights)) / sum(weights)
    # A single pale highlight must not veto a design whose words and main
    # illustration remain visible. Retain a conservative lower weighted
    # percentile, and let *confirmed text ROIs* carry the hard readability gate.
    if important:
        weighted = sorted((v, math.sqrt(n / total)) for v, n in important)
        threshold = .36 * sum(w for _, w in weighted)
        cumulative = 0.0
        primary = weighted[-1][0]
        for value, weight in weighted:
            cumulative += weight
            if cumulative >= threshold:
                primary = value
                break
    else:
        primary = min(values)
    return 100 * (.83 * score + .17 * primary), primary


def _harmony(families: dict, total: int, background: tuple[int, int, int]) -> float:
    """Hue/chroma/tone heuristic informed by CIEDE2000 distance, not exact HCT."""
    bg_ok = _oklab(background)
    bg_lab = _cielab(background)
    chroma = math.hypot(bg_ok[1], bg_ok[2])
    visible = sorted(
        (v for v in families.values() if v["count"] >= max(9, total * .007)),
        key=lambda x: x["count"], reverse=True,
    )[:5]
    if not visible:
        return 60.0
    distances = [delta_e_2000(bg_lab, _cielab(group["mean_rgb"]))
                 for group in visible]
    differences = sum(
        (min(1, delta / 38) for delta in distances),
    ) / len(distances)
    colorful_share = sum(v["count"] for v in visible
                         if math.hypot(*_oklab(v["mean_rgb"])[1:]) > .075) / max(1, total)
    neutral_quiet = _clamp(1 - chroma / .18)
    # Quiet background helps both complex color artwork AND high-contrast
    # monochrome lettering. A saturated garment is still possible when it
    # scores well on foreground visibility, but is not the automatic default.
    quiet = neutral_quiet * (.42 + .58 * min(1, colorful_share * 1.25))
    return 100 * (.55 * differences + .30 * quiet
                  + .15 * _clamp(1 - abs(bg_ok[0] - .56) * .65))


def _refine_small_text(pixels: list, width: int, height: int, regions: list,
                       profiles: list[tuple]) -> tuple[list[tuple], int]:
    """Score each detected text ROI, without overwriting same-family lines.

    A pigment-family aggregate can conceal contrasting strokes on different
    lines (e.g. black and charcoal words). Keep each region's luminance
    bounds and stroke-core alpha as independent readability constraints.
    Large headline ROIs are evaluated too; only microtext increments the
    existing diagnostic counter. No source pixels are changed or persisted.
    """
    requirements = {name: required for name, _luma_value, required, _alpha in profiles}
    by_ink: dict[str, list[tuple]] = defaultdict(list)
    inspected_microtext = 0
    for region in regions[:16]:
        if region["kind"] != "probable_text":
            continue
        ink = region["pigment"]
        if ink not in requirements:
            continue
        x0, y0, x1, y1 = region["bbox"]
        is_microtext = y1 - y0 <= 110
        x0 = max(0, x0 * width // 1000 - 2)
        y0 = max(0, y0 * height // 1000 - 2)
        x1 = min(width, x1 * width // 1000 + 2)
        y1 = min(height, y1 * height // 1000 + 2)
        if x1 <= x0 or y1 <= y0:
            continue
        samples = []
        for y in range(y0, y1, max(1, (y1 - y0) // 50)):
            for x in range(x0, x1, max(1, (x1 - x0) // 80)):
                r, g, b, a = pixels[y * width + x]
                if a >= 32:
                    rgb = (r, g, b)
                    luminance = _luma(rgb)
                    if _family(rgb, luminance) == ink:
                        samples.append((luminance, a / 255))
        if len(samples) < 8:
            continue
        # Reject antialiased edge pixels when estimating the visible
        # stroke color, but preserve genuine faded ink's low core opacity.
        alphas = sorted(alpha for _l, alpha in samples)
        opacity = alphas[int(.75 * (len(alphas) - 1))]
        core_luminances = sorted(
            l for l, alpha in samples if alpha >= max(.12, opacity * .70))
        if len(core_luminances) < 8:
            core_luminances = sorted(l for l, _alpha in samples)
        last = len(core_luminances) - 1
        low = core_luminances[int(.20 * last)]
        high = core_luminances[int(.80 * last)]
        bounds = (low, high) if high - low > .035 else ((low + high) / 2,)
        for luminance in bounds:
            by_ink[ink].append((ink, round(luminance, 6),
                               requirements[ink], round(opacity, 4)))
        if is_microtext:
            inspected_microtext += 1

    refined = []
    for profile in profiles:
        region_probes = by_ink.get(profile[0])
        refined.extend(region_probes if region_probes else [profile])
    return refined, inspected_microtext


def rank_colors_v10(pixels: list[tuple[int, int, int, int]],
                    width: int, height: int, output_sha256: str, *,
                    source: str) -> dict | None:
    """Rank strongest verified options plus a small, explicitly flagged review tier."""
    from .color_match_palette import ALGORITHM

    families, total = _sample_ink(pixels, width, height)
    if not total:
        return None
    region_analysis = analyze_ink_regions(pixels, width, height)
    profiles = region_analysis["profiles"]
    regions = region_analysis["regions"]
    text_kinds = {region["pigment"] for region in regions
                  if region["kind"] == "probable_text"}
    # If morphology cannot confirm a letterform, dark/light pixels are
    # illustration or outline evidence, NOT mandatory text. Never hard-veto
    # a garment merely because pale fur or a dark duck outline blends in.
    fallback_kinds = {
        p[0] for p in profiles
        if p[0] in ("dark-ink", "light-ink")
        and families.get(p[0], {}).get("count", 0) >= max(12, total * .01)
    }
    text_profiles = [p for p in profiles if p[0] in text_kinds]
    fallback_profiles = [p for p in profiles if p[0] in fallback_kinds
                         and p[0] not in text_kinds]
    inspected = 0
    if text_profiles:
        text_profiles, inspected = _refine_small_text(pixels, width, height,
                                                       regions, text_profiles)

    ranked = []
    for color_id, color_hex in GARMENT_COLORS:
        bg = tuple(bytes.fromhex(color_hex[1:]))
        bg_l = _luma(bg)
        constraints = []
        for _name, ink_l, required, opacity in text_profiles:
            real = opacity * ink_l + (1 - opacity) * bg_l
            constraints.append((_contrast(real, bg_l), required, True))
        # Unconfirmed dark/light ink participates in illustration visibility
        # but not the text contrast veto. Review decisions retain the global
        # shape visibility score so important non-text details are not ignored.
        worst_ratio = min((ratio for ratio, _req, _text in constraints), default=10.0)
        margins = [ratio / required for ratio, required, _text in constraints]
        text_margin = min(margins, default=1.0)
        if constraints:
            # Headroom matters: black lettering at 19:1 on White must outrank
            # the same text at barely 4:1 on a saturated shirt. The former
            # V10 normalized both to 100 and then let illustration color
            # difference dominate the final ordering.
            ratios_normalized = [
                .65 * _clamp((ratio - 1) / max(2.0, req - 1))
                + .35 * _clamp(math.log(max(1.0, ratio)) / math.log(14))
                for ratio, req, _text in constraints
            ]
            text_score = 100 * (.64 * min(ratios_normalized)
                                + .36 * sum(ratios_normalized) / len(ratios_normalized))
        else:
            text_score = 0.0
        art_score, primary_visibility = _ink_visibility(families, total, bg)
        harmony_score = _harmony(families, total, bg)
        faded = region_analysis.get("faded_ink", False) or any(
            item[3] < .70 for item in profiles)
        tone = _oklab(bg)[0]
        print_score = 83 - (14 if tone < .38 else 0) - (18 if faded else 0) - (
            8 if source == "thumbnail" else 0)
        if not constraints:
            text_score = art_score
        total_score = (.40 * text_score + .25 * art_score + .25 * harmony_score
                       + .10 * print_score)
        total_score += .55 * SALES_SPEED_PRIOR.get(color_id, 0.0)
        severe = (worst_ratio < 1.65 or primary_visibility < .12)
        high_confidence = (not severe and art_score >= 27 and (
            not constraints or all(ratio >= (3.4 if confirmed else 2.7)
                                   for ratio, _req, confirmed in constraints)))
        # Preview-only: slightly softer score floor, never a high-confidence
        # recommendation. Preserve hard contrast/visibility floors so an
        # unreadable logo is not added merely to fill ten slots.
        conditional = (not severe and art_score >= 23
                       and worst_ratio >= 1.8 and total_score >= 34)
        reason = ("text_low_contrast" if worst_ratio < 1.8 else
                  "illustration_low_contrast" if art_score < 23 else
                  "print_review" if faded or source == "thumbnail" else
                  "text_review" if constraints and not high_confidence else
                  "low_rank")
        ranked.append({
            "id": color_id, "score": round(total_score, 2),
            "visual": round(art_score, 2), "text_margin": round(text_margin, 3),
            "high_confidence": high_confidence, "conditional": conditional,
            "reason": reason,
        })
    ranked.sort(key=lambda x: (-x["score"], -x["visual"], x["id"]))
    eligible = [row["id"] for row in ranked if row["high_confidence"]]
    recommendations = eligible[:10]
    review = []
    if len(recommendations) < 7:
        review_candidates = [row for row in ranked
                             if row["conditional"] and not row["high_confidence"]]
        top_score = max((row["score"] for row in ranked), default=0)
        for row in review_candidates:
            if len(review) >= min(5, 10 - len(recommendations)):
                break
            if row["score"] >= max(34, .78 * top_score):
                review.append(row["id"])
        recommendations.extend(review)
    unsafe = [row["id"] for row in ranked
              if row["id"] not in eligible and row["id"] not in review]
    reasons = {row["id"]: row["reason"] for row in ranked}
    lookup = {row["id"]: row for row in ranked}
    return {
        "version": ALGORITHM,
        "output_sha256": output_sha256,
        "recommended": recommendations,
        "eligible_colors": eligible,
        "review_colors": review,
        "unsafe_colors": unsafe,
        "scores": {c: lookup[c]["score"] for c in recommendations},
        "contrast_scores": {c: lookup[c]["visual"] for c in recommendations},
        "readability_margins": {c: lookup[c]["text_margin"] for c in recommendations},
        "readability_check": "role_salience_alpha_composite_v4",
        "method": "role_aware_salience_ciede2000_v10",
        "analysis": {
            "resolution": [width, height], "source": source,
            "regions": regions[:12],
            "text_regions": region_analysis.get("text_regions", 0),
            "microtext_regions": inspected,
            "faded_ink": bool(region_analysis.get("faded_ink", False)),
            "review_policy": "guarded-preview-floor-v2",
            "rank_policy": "text-roi-soft-art-v4",
            "print_review_required": bool(region_analysis.get("faded_ink", False) or source == "thumbnail"
                                          or review or inspected),
        },
        "color_reasons": reasons,
        "role_counts": {"text": len(text_profiles), "illustration": len(
            [x for x in families if x not in text_kinds]),
                        "details": len(fallback_profiles)},
        "popularity_source": SALES_PRIOR_SNAPSHOT,
        "insufficient_safe_colors": len(eligible) < 7,
    }
