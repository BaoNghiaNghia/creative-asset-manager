"""Bounded design-aware ink/readability analysis for ColorMatch V7.

Pixel shapes are only typography proxies, NOT OCR; no generated art, no
text/people identification, no external model. Sparse/faded ink is evaluated
after compositing over the actual candidate garment swatch.
"""
from __future__ import annotations

import colorsys
from collections import defaultdict
from typing import Any

_MAX_SHAPES = 3000
_MIN_ALPHA = 32


def _linear(value: int) -> float:
    v = value / 255.0
    return v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_linear(v) for v in rgb)
    return .2126 * r + .7152 * g + .0722 * b


def _contrast(a: float, b: float) -> float:
    return (max(a, b) + .05) / (min(a, b) + .05)


def _shapes(mask: bytearray, width: int, height: int) -> list[tuple[int, int, int, int, int]]:
    visited = bytearray(len(mask))
    shapes: list[tuple[int, int, int, int, int]] = []
    seen = 0
    for start, marked in enumerate(mask):
        if not marked or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        x0 = x1 = start % width
        y0 = y1 = start // width
        area = 0
        while stack:
            point = stack.pop()
            y, x = divmod(point, width)
            area += 1
            x0, x1 = min(x0, x), max(x1, x)
            y0, y1 = min(y0, y), max(y1, y)
            for dy in (-1, 0, 1):
                ny = y + dy
                if ny < 0 or ny >= height:
                    continue
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if nx < 0 or nx >= width or (dx == 0 and dy == 0):
                        continue
                    other = ny * width + nx
                    if mask[other] and not visited[other]:
                        visited[other] = 1
                        stack.append(other)
        seen += 1
        if seen > _MAX_SHAPES:
            break
        sx, sy = x1 - x0 + 1, y1 - y0 + 1
        if (area >= 2 and sx <= max(5, width // 7) and sy <= max(5, height // 7)
                and sx <= sy * 3 and sy <= sx * 8
                and sy >= 2 and area / (sx * sy) <= .73):
            shapes.append((x0, y0, x1, y1, area))
    return shapes


def _text_line_boxes(
    shapes: list[tuple[int, int, int, int, int]], width: int, height: int,
) -> list[tuple[int, int, int, int]]:
    """Group similarly sized glyph-sized islands sharing a baseline."""
    if len(shapes) < 4:
        return []
    sorted_shapes = sorted(shapes, key=lambda p: (p[1] + p[3]) / 2)
    bands: list[tuple[int, int, int, int]] = []
    tolerance = max(3, int(height / 42))
    for n, item in enumerate(sorted_shapes):
        cy = (item[1] + item[3]) / 2
        close = [shape for shape in sorted_shapes[n:]
                 if abs((shape[1] + shape[3]) / 2 - cy) <= tolerance]
        if len(close) < 4:
            continue
        left, top = min(s[0] for s in close), min(s[1] for s in close)
        right, bottom = max(s[2] for s in close), max(s[3] for s in close)
        if right - left < width * .14:
            continue
        region = (left, top, right, bottom)
        if all(abs(region[0] - b[0]) > tolerance or
               abs(region[1] - b[1]) > tolerance for b in bands):
            bands.append(region)
        if len(bands) >= 12:
            break
    return bands


def _text_like(mask: bytearray, width: int, height: int) -> bool:
    return bool(_text_line_boxes(_shapes(mask, width, height), width, height))


def _norm_bbox(box: tuple[int, int, int, int], width: int, height: int) -> list[int]:
    x0, y0, x1, y1 = box
    return [min(1000, x0 * 1000 // max(1, width)),
            min(1000, y0 * 1000 // max(1, height)),
            min(1000, (x1 + 1) * 1000 // max(1, width)),
            min(1000, (y1 + 1) * 1000 // max(1, height))]


def analyze_ink_regions(
    pixels: list[tuple[int, int, int, int]], width: int, height: int,
) -> dict[str, Any]:
    """Create compact bounded ink profiles and coarse spatial text/illustration ROIs.

    Keep actual pixels only in this function's memory. Return neither text nor
    original image data. The caller can cache this result by immutable SHA.
    """
    if not width or not height or len(pixels) != width * height:
        return {"profiles": [], "regions": [], "faded_ink": False}
    n = len(pixels)
    groups: dict[str, list[tuple[float, float]]] = defaultdict(list)
    masks: dict[str, bytearray] = {}
    total_ink = 0
    for idx, (r, g, b, a) in enumerate(pixels):
        if a < _MIN_ALPHA:
            continue
        total_ink += 1
        luma = _luminance((r, g, b))
        chroma = max(r, g, b) - min(r, g, b)
        if luma <= .15 and chroma <= 90:
            kind = "dark-ink"
        elif luma >= .68 and chroma <= 90:
            kind = "light-ink"
        elif chroma <= 28:
            kind = "mid-neutral"
        else:
            hue, _sat, _value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            kind = f"chromatic-{round(hue * 12) % 12}-{min(4, int(luma * 5))}"
        groups[kind].append((luma, a / 255.0))
        mask = masks.get(kind)
        if mask is None:
            mask = bytearray(n)
            masks[kind] = mask
        mask[idx] = 1
    if total_ink < 12:
        return {"profiles": [], "regions": [], "faded_ink": False}

    profiles: list[tuple[str, float, float, float]] = []
    regions: list[dict[str, Any]] = []
    faded = False
    for kind, values in groups.items():
        if len(values) < 12:
            continue
        lines = _text_line_boxes(_shapes(masks[kind], width, height), width, height)
        text_like = bool(lines)
        # Hard-gate dark/light ink even when thin type is not confidently
        # recognized. Colored illustration does not become text merely
        # because its pigment is abundant.
        if kind in ("dark-ink", "light-ink"):
            if len(values) < max(10, int(total_ink * (.004 if text_like else .015))):
                continue
            required = 4.5 if text_like else 3.0
        elif not text_like or len(values) < max(12, int(total_ink * .012)):
            continue
        else:
            required = 3.0
        luminance = sorted(v[0] for v in values)[len(values) // 4]
        opacity = sorted(v[1] for v in values)[len(values) // 2]
        if opacity < .7:
            faded = True
        profiles.append((kind, round(luminance, 6), required, round(opacity, 4)))
        for bbox in lines[:3]:
            regions.append({
                "kind": "probable_text", "pigment": kind,
                "bbox": _norm_bbox(bbox, width, height),
                "opacity": round(opacity, 2),
                "confidence": "proxy",
            })
        if not lines and kind in ("dark-ink", "light-ink"):
            indices = [i for i, marked in enumerate(masks[kind]) if marked]
            if indices:
                coords = [(i % width, i // width) for i in indices]
                bbox = (min(pt[0] for pt in coords), min(pt[1] for pt in coords),
                        max(pt[0] for pt in coords), max(pt[1] for pt in coords))
                regions.append({
                    "kind": "critical_ink", "pigment": kind,
                    "bbox": _norm_bbox(bbox, width, height),
                    "opacity": round(opacity, 2), "confidence": "proxy",
                })
    return {
        "profiles": profiles[:18],
        "regions": regions[:16],
        "faded_ink": faded,
        "analysis_width": width,
        "analysis_height": height,
        "text_regions": sum(x["kind"] == "probable_text" for x in regions),
    }


def readability_profiles(
    pixels: list[tuple[int, int, int, int]], width: int, height: int,
) -> list[tuple[str, float, float, float]]:
    return analyze_ink_regions(pixels, width, height)["profiles"]


def candidate_readability(
    profiles: list[tuple[str, float, float, float]],
    background_rgb: tuple[int, int, int],
) -> tuple[bool, float]:
    if not profiles:
        return True, 1.0
    background = _luminance(background_rgb)
    ratios: list[float] = []
    for profile in profiles:
        ink, minimum = profile[1], profile[2]
        opacity = profile[3] if len(profile) > 3 else 1.0
        # Compare what the viewer actually sees when semi-transparent ink
        # is composited on garment color, not the source RGB in isolation.
        effective = opacity * ink + (1 - opacity) * background
        required = max(1.3, 1.2 + (minimum - 1.2) * opacity ** 2.5)
        ratios.append(_contrast(effective, background) / required)
    margin = min(ratios)
    return margin >= 1, round(margin, 3)
