"""Slot 1 native macro reuse and later-angle original-stock safety gates."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc.stage2_stock_inpaint import (
    Stage2InpaintError, native_thread_macro, composite_original_stock, verified_inpaint_result,
)


def png(color, *, size=(1024, 1024), patch=None):
    img = Image.new("RGB", size, color)
    if patch is not None:
        ImageDraw.Draw(img).rectangle(patch, fill=(15, 35, 180))
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def candidate(content, sha=None):
    return SimpleNamespace(
        image_bytes=content,
        provider_metadata={"native_sha256": sha or sha256(content).hexdigest()},
    )


def test_native_macro_remains_exact_png_without_jpeg_reencode():
    original = png((240, 232, 210))
    prepared = native_thread_macro(original)
    assert prepared.image_bytes == original
    assert prepared.mime_type == "image/png"


def test_reject_noop_or_unverified_native_photograph():
    raw = png((240, 232, 210))
    with pytest.raises(Stage2InpaintError, match="did not apply"):
        verified_inpaint_result(raw, candidate(raw), "stock")
    altered = png((225, 234, 231))
    with pytest.raises(Stage2InpaintError, match="provenance"):
        verified_inpaint_result(raw, candidate(altered, "0" * 64), "stock")


@pytest.mark.parametrize("stage,patch", [
    ("prod_s2_ec2", (350, 350, 450, 430)),
    ("prod_s2_ec3", (755, 360, 820, 425)),
])
def test_exact_original_stock_pixels_outside_front_crown(stage, patch):
    stock = png((245, 231, 214))
    edited = png((245, 231, 214), patch=patch)
    final = composite_original_stock(stock, candidate(edited), stage)
    with Image.open(BytesIO(stock)) as im:
        before = np.asarray(im).copy()
    with Image.open(BytesIO(final)) as im:
        after = np.asarray(im).copy()
    assert tuple(after.shape) == tuple(before.shape)
    assert np.array_equal(before[10:100, 10:100], after[10:100, 10:100])
    assert np.array_equal(before[750:900, 100:500], after[750:900, 100:500])
    assert np.any(before[patch[1]:patch[3], patch[0]:patch[2]] !=
                  after[patch[1]:patch[3], patch[0]:patch[2]])


def test_scene_inpaint_rejects_full_reshoot_or_noop():
    base = png((240, 230, 214))
    reshoot = png((15, 30, 50))
    with pytest.raises(Stage2InpaintError, match="repainted too much"):
        verified_inpaint_result(base, candidate(reshoot), "scene")
    with pytest.raises(Stage2InpaintError, match="did not apply"):
        verified_inpaint_result(base, candidate(base), "scene")
