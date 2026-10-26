"""Realistic satin-thread shading for Stage 1 typography using Pillow only.

The shape is always owned by the approved typography font renderer: this
shader cannot invent, erase or reshape a glyph. It works on a bounded ink
crop, not on the entire 2048x1024 frame, and keeps the exact alpha bytes.
A deterministic angled satin-thread pattern plus directional edge relief
produces a legible embroidery appearance without Codex, NumPy or OpenCV.
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageFilter


def embroider_typography(flat: Image.Image, *, concept: int) -> Image.Image:
    if flat.mode != "RGBA":
        raise ValueError("Stage 1 embroidery renderer requires an RGBA master")
    alpha = flat.getchannel("A")
    bound = alpha.getbbox()
    if bound is None:
        raise ValueError("Cannot stitch an empty typography master")
    pad = 6
    left, top, right, bottom = bound
    box = (max(0, left - pad), max(0, top - pad),
           min(flat.width, right + pad), min(flat.height, bottom + pad))
    cut = flat.crop(box)
    mask = cut.getchannel("A")
    width, height = cut.size
    # Two scales of angled satin thread: broader stitched bundles must
    # survive UI downsampling, while individual needle ridges are visible
    # at the full 2048px size. Both fields stay strictly inside glyph alpha.
    directions = (0.27, -0.32, 0.38, -0.26, 0.31, -0.38)
    slope = directions[(concept - 1) % 6]
    spread = int(abs(slope) * height) + 24
    bundles = Image.new("L", cut.size, 230)
    broad = ImageDraw.Draw(bundles)
    for start in range(-spread, width + spread, 26):
        broad.line([(start, 0), (round(start + slope * height), height)],
                   fill=255, width=9)
        broad.line([(start + 13, 0),
                    (round(start + 13 + slope * height), height)],
                   fill=145, width=9)
    fibers = Image.new("L", cut.size, 230)
    fine = ImageDraw.Draw(fibers)
    for start in range(-spread, width + spread, 4):
        fine.line([(start, 0), (round(start + slope * height), height)],
                  fill=255, width=1)
        fine.line([(start + 2, 0),
                   (round(start + 2 + slope * height), height)],
                  fill=173, width=1)
    lighting = ImageChops.multiply(bundles, fibers)
    # Multiply the two-color originals by the same light field. Reds stay red;
    # royal blue keeps its hue, rather than being flattened to grayscale.
    source_rgb = cut.convert("RGB")
    lit_rgb = Image.merge("RGB", tuple(
        ImageChops.multiply(channel, lighting)
        for channel in source_rgb.split()
    ))
    # Raised satin edge: light hits top-left lips and casts a tiny shadow
    # toward bottom-right. Only pixels already inside the source alpha change.
    upper = ImageChops.subtract(mask, ImageChops.offset(mask, 2, 2))
    lower = ImageChops.subtract(mask, ImageChops.offset(mask, -2, -2))
    upper = ImageChops.multiply(upper, mask).point(
        [min(255, round(v * 0.44)) for v in range(256)])
    lower = ImageChops.multiply(lower, mask).point(
        [min(255, round(v * 0.43)) for v in range(256)])
    lit_rgb = Image.composite(Image.new("RGB", cut.size, (230, 233, 250)),
                              lit_rgb, upper)
    lit_rgb = Image.composite(Image.new("RGB", cut.size, (17, 23, 59)),
                              lit_rgb, lower)
    lit_rgb.putalpha(mask)
    output = Image.new("RGBA", flat.size, (0, 0, 0, 0))
    output.paste(lit_rgb, box[:2])
    if output.getchannel("A").tobytes() != alpha.tobytes():
        raise ValueError("Stage 1 stitch shader changed the typography outline")
    return output