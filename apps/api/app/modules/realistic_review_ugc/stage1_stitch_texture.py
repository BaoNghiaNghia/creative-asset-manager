"""Realistic satin-thread shading for Stage 1 typography using Pillow only.

The shape is always owned by the approved typography font renderer: this
shader cannot invent, erase or reshape a glyph. It works on a bounded ink
crop, not on the entire 2048x1024 frame, and keeps the exact alpha bytes.
A deterministic angled satin-thread pattern plus directional edge relief
produces a legible embroidery appearance without Codex, NumPy or OpenCV.
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageFilter
from app.modules.realistic_review_ugc.stage1_photo_samples import reference_patch


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
    # Local satin thread directions replace one repeated angled texture;
    # individual fine needle ridges remain visible
    # at the full 2048px size. Both fields stay strictly inside glyph alpha.
    # Derive a local transverse stitch direction from glyph-edge geometry.
    # Vertical strokes get horizontal thread rows; horizontal strokes get
    # vertical thread rows. Curved serifs interpolate between both.
    x_edges = ImageChops.add(
        ImageChops.difference(mask, ImageChops.offset(mask, 7, 0)),
        ImageChops.difference(mask, ImageChops.offset(mask, -7, 0)),
        scale=1.7).filter(ImageFilter.GaussianBlur(23))
    y_edges = ImageChops.add(
        ImageChops.difference(mask, ImageChops.offset(mask, 0, 7)),
        ImageChops.difference(mask, ImageChops.offset(mask, 0, -7)),
        scale=1.7).filter(ImageFilter.GaussianBlur(23))
    # Amplify directional evidence so wide stroke interiors do not average
    # both textures into a crosshatch (which resembles printed graphics).
    x_strength = ImageChops.subtract(x_edges, y_edges).point(
        [min(255, v * 32) for v in range(256)])
    y_strength = ImageChops.subtract(y_edges, x_edges).point(
        [min(255, v * 32) for v in range(256)])
    orientation = ImageChops.subtract(
        ImageChops.add(Image.new("L", cut.size, 128), x_strength),
        y_strength)
    horizontal = Image.new("L", cut.size, 235)
    vertical = Image.new("L", cut.size, 235)
    hpen, vpen = ImageDraw.Draw(horizontal), ImageDraw.Draw(vertical)
    for y in range(0, height + 3, 4):
        hpen.line(((0, y), (width, y)), fill=255, width=1)
        hpen.line(((0, y + 2), (width, y + 2)), fill=173, width=1)
    for x in range(0, width + 3, 4):
        vpen.line(((x, 0), (x, height)), fill=255, width=1)
        vpen.line(((x + 2, 0), (x + 2, height)), fill=173, width=1)
    yarn = reference_patch("thread").point(
        [min(250, 170 + int(v * .85)) for v in range(256)])
    photo_h = Image.new("L", cut.size)
    photo_v = Image.new("L", cut.size)
    yarn_v = yarn.transpose(Image.Transpose.ROTATE_90)
    for yy in range(0, height, 64):
        for xx in range(0, width, 64):
            photo_h.paste(yarn, (xx, yy))
            photo_v.paste(yarn_v, (xx, yy))
    fine = Image.composite(horizontal, vertical, orientation)
    photo = Image.composite(photo_h, photo_v, orientation)
    lighting = Image.blend(fine, photo, 0.78)
    # Multiply the two-color originals by the same light field. Reds stay red;
    # royal blue keeps its hue, rather than being flattened to grayscale.
    source_rgb = cut.convert("RGB")
    lit_rgb = Image.merge("RGB", tuple(
        ImageChops.multiply(channel, lighting)
        for channel in source_rgb.split()
    ))
    # Satin yarn reflects tiny colored specular glints. Multiplication alone
    # only darkens paint and looks like a flat printed decal.
    highlights = ImageChops.subtract(
        photo, Image.new("L", cut.size, 216)
    ).point([min(255, round(v * 1.2)) for v in range(256)])
    glints = Image.merge("RGB", (
        highlights.point([round(v * .58) for v in range(256)]),
        highlights.point([round(v * .75) for v in range(256)]),
        highlights,
    ))
    lit_rgb = ImageChops.add(lit_rgb, glints)
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