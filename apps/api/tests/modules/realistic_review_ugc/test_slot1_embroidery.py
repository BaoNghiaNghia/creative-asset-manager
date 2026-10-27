"""Stage 2 Products Slot 1 material pass and fail-closed photo refinement."""
from io import BytesIO

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc.front_placement import DEFAULT_FRONT_PLACEMENT
from app.modules.realistic_review_ugc.slot1_embroidery import (
    Slot1EmbroideryQAError, build_slot1_material_pass, finish_slot1_refinement,
)


def png(image):
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.fixture()
def material():
    width, height = 512, 640
    # A real-looking fixed weave, stitched brim, and factory logo, not a flat
    # single-color test where an accidental repaint might go unnoticed.
    yy, xx = np.indices((height, width))
    weave = (np.sin(xx * .9 + yy * .45) * 2.0 + np.cos(yy * .55) * 1.5)
    stock = np.empty((height, width, 3), np.uint8)
    for color, level in enumerate((225, 218, 205)):
        stock[:, :, color] = np.uint8(np.clip(level + weave, 0, 255))
    stock[350:385, 477:492] = (25, 45, 60)  # Original small side mark.
    cap = Image.fromarray(stock, "RGB")
    art = Image.new("RGBA", (480, 220), (0, 0, 0, 0))
    draw = ImageDraw.Draw(art)
    draw.rectangle((15, 25, 450, 95), fill=(12, 55, 176, 255))
    draw.line(((48, 140), (430, 170)), fill=(8, 72, 190, 255), width=11)
    draw.arc((95, 118, 178, 208), 0, 260, fill=(8, 72, 190, 255), width=8)
    placement = {**DEFAULT_FRONT_PLACEMENT, "design_size": 100}
    return build_slot1_material_pass(png(cap), png(art), placement)


def test_underlay_preserves_stock_and_original_placement(material):
    orig = np.asarray(material.stock)
    new = np.asarray(material.stitched)
    mask = np.asarray(material.edit_mask) > 0
    assert material.stock.size == material.stitched.size == (512, 640)
    assert np.array_equal(orig[~mask], new[~mask])
    assert np.count_nonzero(orig[mask] != new[mask]) > 500
    assert np.array_equal(orig[350:385, 477:492], new[350:385, 477:492])
    x0, y0, x1, y1 = material.guide_box
    assert not np.any(mask[:y0])
    assert not np.any(mask[y1:])
    assert not np.any(mask[:, :x0])
    assert not np.any(mask[:, x1:])
    # Dense fill contains fine directional thread modulation.
    core = np.asarray(material.thread_mask) >= 220
    values = new[core][:, 2].astype(float)
    assert values.std() > .2
    assert np.all((new[core][:, 2] > new[core][:, 0]))


def test_second_pass_accepts_refinement_but_rejects_hat_drift(material):
    result = finish_slot1_refinement(material, material.stitched_png())
    with Image.open(BytesIO(result)) as image:
        image.load()
        assert image.format == "PNG" and image.size == material.stock.size
        final = np.asarray(image)
    original = np.asarray(material.stock)
    mask = np.asarray(material.edit_mask) > 0
    assert np.array_equal(final[~mask], original[~mask])
    assert np.any(final[mask] != original[mask])
    assert np.array_equal(original[350:385, 477:492], final[350:385, 477:492])


def test_second_pass_rejects_blank_photo_without_original_design(material):
    with pytest.raises(Slot1EmbroideryQAError, match="lost the original design"):
        finish_slot1_refinement(material, png(material.stock))


def test_second_pass_rejects_wrong_pigment(material):
    original = np.asarray(material.stitched, dtype=np.uint8).copy()
    core = np.asarray(material.thread_mask) >= 170
    original[core] = (246, 205, 49)
    with pytest.raises(Slot1EmbroideryQAError, match="lost the original design"):
        finish_slot1_refinement(material, png(Image.fromarray(original, "RGB")))


def test_halo_in_candidate_cannot_spread_outside_edit_mask(material):
    cap = np.asarray(material.stitched, dtype=np.uint8).copy()
    core = np.uint8(np.asarray(material.thread_mask) > 127)
    near = cv2.dilate(core, np.ones((7, 7), np.uint8)).astype(bool) & ~(core.astype(bool))
    # Even a bad candidate must never yield a heavy embossed outline in the
    # saved PNG: either reject or preserve the masked source geometry.
    cap[near] = np.maximum(0, cap[near].astype(np.int16) - 70).astype(np.uint8)
    with pytest.raises(Slot1EmbroideryQAError, match="emboss halo"):
        finish_slot1_refinement(material, png(Image.fromarray(cap, "RGB")))
