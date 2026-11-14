"""Conservative print-ink legibility guard for C1717 garment backgrounds.

No OCR or inference about the exact words. This is a pixel- and connected-
component text-likeness proxy computed on the *existing* RGBA thumbnail.
A garment failing the guard can never be promoted by demand ranking.
"""
from __future__ import annotations

import colorsys
import math
from collections import defaultdict

_MAX_SHAPES = 2500


def _linear(value: int) -> float:
    v = value / 255.0
    return v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_linear(v) for v in rgb)
    return .2126 * r + .7152 * g + .0722 * b


def _contrast(a: float, b: float) -> float:
    return (max(a, b) + .05) / (min(a, b) + .05)


def _text_like(mask: bytearray, width: int, height: int) -> bool:
    """Identify multiple letter-sized ink islands sharing a horizontal line.

    The check is intentionally morphological, not OCR. At thumbnail scale
    illustrations can produce false positives, which safely errs toward
    excluding an otherwise attractive but hard-to-read dark garment.
    """
    if not any(mask):
        return False
    visited = bytearray(len(mask))
    shapes: list[tuple[float, int, int]] = []
    explored = 0
    for start, ink in enumerate(mask):
        if not ink or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        min_x = max_x = start % width
        min_y = max_y = start // width
        area = 0
        while stack:
            idx = stack.pop()
            y, x = divmod(idx, width)
            area += 1
            min_x, max_x = min(x, min_x), max(x, max_x)
            min_y, max_y = min(y, min_y), max(y, max_y)
            for dy in (-1, 0, 1):
                ny = y + dy
                if ny < 0 or ny >= height:
                    continue
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if nx < 0 or nx >= width or (dx == 0 and dy == 0):
                        continue
                    nxt = ny * width + nx
                    if mask[nxt] and not visited[nxt]:
                        visited[nxt] = 1
                        stack.append(nxt)
        explored += 1
        if explored > _MAX_SHAPES:
            break
        span_x, span_y = max_x - min_x + 1, max_y - min_y + 1
        if (area >= 2 and span_x <= max(5, width // 7)
                and span_y <= max(5, height // 7)
                and span_x <= span_y * 3.0
                and span_y <= span_x * 8.0
                and span_y >= 2
                # Solid petals, balloons and animal blobs should not be
                # classified as printed letters just because they are small.
                and area / (span_x * span_y) <= .70):
            shapes.append(((min_y + max_y) / 2, min_x, max_x))
    if len(shapes) < 5:
        return False
    shapes.sort()
    tolerance = max(3.5, height / 36)
    for i, (cy, _left, _right) in enumerate(shapes):
        matching = [shape for shape in shapes[i:] if shape[0] - cy <= tolerance * 2]
        if len(matching) < 5:
            continue
        if max(s[2] for s in matching) - min(s[1] for s in matching) >= width * .18:
            return True
    return False


def readability_profiles(
    pixels: list[tuple[int, int, int, int]], width: int, height: int,
) -> list[tuple[str, float, float]]:
    """Meaningful ink pigments with minimum required WCAG-style contrast.

    Protect dark/light neutral lettering even when its area is small relative
    to a bright illustration. Other frequent colored text-like pigments also
    receive a contrast gate. Does not claim to identify actual characters.
    """
    n = len(pixels)
    masks: dict[str, bytearray] = {}
    groups: dict[str, list[float]] = defaultdict(list)
    opaque_count = 0
    for idx, (r, g, b, a) in enumerate(pixels):
        if a < 155:
            continue
        opaque_count += 1
        rgb = (r, g, b)
        luma = _luminance(rgb)
        chroma = max(rgb) - min(rgb)
        if luma <= .15 and chroma <= 90:
            kind = "dark-ink"
        elif luma >= .68 and chroma <= 90:
            kind = "light-ink"
        else:
            hue, sat, _value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            # Chromatic groups use a coarser hue/brightness bucket. Near-grey
            # middles are not treated as text without multiple glyphs.
            kind = f"pigment-{round(hue * 12) % 12}-{int(min(4, luma * 5))}" if sat > .18 else "mid-neutral"
        groups[kind].append(luma)
        if kind not in masks:
            masks[kind] = bytearray(n)
        masks[kind][idx] = 1
    if opaque_count < 12:
        return []
    significant: list[tuple[str, float, float]] = []
    for kind, values in groups.items():
        count = len(values)
        glyphs = _text_like(masks[kind], width, height) if count >= 12 else False
        if kind in ("dark-ink", "light-ink"):
            if count < max(10, opaque_count * (.006 if glyphs else .018)):
                continue
            required = 4.5 if glyphs else 3.2
        else:
            # Colorful illustrations can occupy most of the artwork and
            # need not be legible as text. Never make every garment fail
            # because a large flamingo/animal contains many bright pigments.
            # Saturated or mid-grey ink gets a hard gate only when it forms
            # aligned glyph-sized components (likely colored typography).
            if not glyphs or count < max(12, opaque_count * .012):
                continue
            required = 3.0
        sorted_values = sorted(values)
        # The 25th-percentile pigment controls the gate; this protects tiny
        # near-black/near-white text without using a few antialias outliers.
        representative = sorted_values[len(sorted_values) // 4]
        significant.append((kind, representative, required))
    return significant


def candidate_readability(
    profiles: list[tuple[str, float, float]], background_rgb: tuple[int, int, int],
) -> tuple[bool, float]:
    """Return hard-gate verdict and worst normalized ink contrast ratio."""
    if not profiles:
        return True, 1.0
    background = _luminance(background_rgb)
    margin = min(_contrast(ink, background) / minimum for _kind, ink, minimum in profiles)
    return margin >= 1.0, round(margin, 3)
