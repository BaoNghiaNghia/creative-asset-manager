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


def refined_candidate(material, square=False):
    """A test-only stand-in for an AI edit with newly resolved yarn fibers."""
    if square:
        base = np.array(Image.open(BytesIO(material.model_edit_target_png())), dtype=np.int16)
        mask = np.array(Image.open(BytesIO(material.edit_mask_png())), dtype=np.uint8) > 200
    else:
        base = np.asarray(material.stitched, dtype=np.int16).copy()
        mask = np.asarray(material.thread_mask, dtype=np.uint8) > 200
    rng = np.random.default_rng(47)
    if material.edit_roi_box is not None:
        # Realistic test-only coherent fine fibers, not random pixel noise.
        yy, xx = np.indices(mask.shape)
        texture = (np.sin(xx * .33 * 2 * np.pi + yy * .11 * 2 * np.pi) * 11
                   + rng.normal(0, 2, mask.shape))
    else:
        texture = rng.normal(0, 13, mask.shape)
    base[mask] += np.rint(texture[mask])[:, None].astype(np.int16)
    return png(Image.fromarray(np.uint8(np.clip(base, 0, 255)), "RGB"))


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


def test_slot1_flattens_fake_bevel_lighting_without_changing_letter_shape():
    stock = Image.new("RGB", (512, 640), (232, 219, 203))
    art = Image.new("RGBA", (420, 180))
    pixels = np.zeros((180, 420, 4), dtype=np.uint8)
    # A simulated glossy embossed Stage 1 blue glyph: same hue, 3D light/dark
    # RGB gradient across a single stroke. Stage 2 must not inherit it.
    for x in range(40, 380):
        level = 0.52 + .85 * ((x - 40) / 340)
        pixels[32:125, x, :3] = np.uint8(np.clip(
            np.array([27, 65, 177]) * level, 0, 255))
        pixels[32:125, x, 3] = 255
    art = Image.fromarray(pixels, "RGBA")
    result = build_slot1_material_pass(
        png(stock), png(art), {**DEFAULT_FRONT_PLACEMENT, "design_size": 100})
    inside = np.asarray(result.thread_mask) > 245
    colors = np.asarray(result.stitched, dtype=np.float32)[inside]
    assert len(colors) > 500
    # Fine stitch contrast is intentional; the original *broad* embossed
    # brightness gradient must disappear without flattening all filament grain.
    channel = np.asarray(result.stitched, dtype=np.float32)[:, :, 2]
    smooth = cv2.GaussianBlur(channel, (0, 0), 5)
    assert float(np.std(smooth[inside])) < 9.0, "Stage 1 broad bevel survived"
    highpass = channel - cv2.GaussianBlur(channel, (0, 0), .95)
    assert float(np.std(highpass[inside])) < 3.0, "Pigment-only guide must not contain invented stitch texture"
    assert np.all(np.asarray(result.stitched)[~np.asarray(result.edit_mask).astype(bool)]
                  == np.asarray(result.stock)[~np.asarray(result.edit_mask).astype(bool)])


def test_second_pass_accepts_refinement_but_rejects_hat_drift(material):
    result = finish_slot1_refinement(material, refined_candidate(material))
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
    with pytest.raises(Slot1EmbroideryQAError, match="canonical design"):
        finish_slot1_refinement(material, png(material.stock))


def test_second_pass_rejects_wrong_pigment(material):
    original = np.asarray(material.stitched, dtype=np.uint8).copy()
    core = np.asarray(material.thread_mask) >= 170
    original[core] = (246, 205, 49)
    with pytest.raises(Slot1EmbroideryQAError, match="canonical design"):
        finish_slot1_refinement(material, png(Image.fromarray(original, "RGB")))


def test_halo_in_candidate_cannot_spread_outside_edit_mask(material):
    cap = np.asarray(Image.open(BytesIO(refined_candidate(material))), dtype=np.uint8).copy()
    core = np.uint8(np.asarray(material.thread_mask) > 127)
    near = cv2.dilate(core, np.ones((7, 7), np.uint8)).astype(bool) & ~(core.astype(bool))
    # Even a bad candidate must never yield a heavy embossed outline in the
    # saved PNG: either reject or preserve the masked source geometry.
    cap[near] = np.maximum(0, cap[near].astype(np.int16) - 70).astype(np.uint8)
    with pytest.raises(Slot1EmbroideryQAError, match="shadow halo"):
        finish_slot1_refinement(material, png(Image.fromarray(cap, "RGB")))


def test_stitch_material_has_visible_nonflat_filaments_and_is_deterministic(material):
    before = np.asarray(material.stock, dtype=np.float32)
    stitched = np.asarray(material.stitched, dtype=np.float32)
    core = np.asarray(material.thread_mask) > 240
    assert core.sum() > 100
    changed = stitched - before
    # Real thread shows fine texture inside the stroke, not a single solid
    # pixel color or an oversized bevel outside the source silhouette.
    bright = cv2.cvtColor(np.uint8(stitched), cv2.COLOR_RGB2GRAY).astype(np.float32)
    micro = bright - cv2.GaussianBlur(bright, (0, 0), 1.15)
    interior = cv2.erode(core.astype(np.uint8), np.ones((13, 13), np.uint8)).astype(bool)
    assert np.count_nonzero(interior) > 100
    assert float(np.std(micro[interior])) < 3.0  # Guide has no fake stitch texture
    assert abs(float(np.mean(changed[core, 2]))) > 10
    assert material.guide_box[0] >= 0


def test_second_pass_retains_bounded_fiber_detail_without_painting_another_word(material):
    before = np.asarray(material.stitched, dtype=np.uint8).copy()
    stock = np.asarray(material.stock, dtype=np.uint8)
    core = np.asarray(material.thread_mask) >= 230
    yy, xx = np.indices(core.shape)
    variant = before.astype(np.int16)
    variant[core] += np.where(xx[core] % 4 < 2, 12, -12)[:, None]
    variant = np.uint8(np.clip(variant, 0, 255))
    output = finish_slot1_refinement(material, png(Image.fromarray(variant, "RGB")))
    final = np.asarray(Image.open(BytesIO(output)))
    editable = np.asarray(material.edit_mask) > 0
    assert np.array_equal(final[~editable], stock[~editable])
    assert not np.array_equal(final[core], before[core])
    # Contrast must remain restrained; secondary pass cannot puff the letters.
    assert np.max(np.abs(final[core].astype(np.int16) - before[core].astype(np.int16))) <= 50


def test_second_pass_rejects_untextured_print_even_with_valid_source_shape(material):
    from dataclasses import replace

    alpha = np.asarray(material.thread_mask, dtype=np.float32) / 255.0
    stock = np.asarray(material.stock, dtype=np.float32)
    pigment = np.array([12, 55, 176], dtype=np.float32)
    matte = stock * (1 - alpha[..., None]) + pigment * alpha[..., None]
    flat = Image.fromarray(np.uint8(np.clip(matte, 0, 255)), "RGB")
    flat_pass = replace(material, stitched=flat)
    with pytest.raises(Slot1EmbroideryQAError, match="no meaningful photographic refinement|flat printed"):
        finish_slot1_refinement(flat_pass, png(flat))


def test_no_op_model_edit_fails_closed(material):
    with pytest.raises(Slot1EmbroideryQAError, match="no meaningful"):
        finish_slot1_refinement(material, material.stitched_png())


def test_square_model_canvas_restores_exact_stock_dimensions_and_protected_pixels(material):
    edited = refined_candidate(material, square=True)
    result = Image.open(BytesIO(finish_slot1_refinement(material, edited)))
    assert result.size == material.stock.size
    mask = np.asarray(material.edit_mask)>0
    assert np.array_equal(np.asarray(result)[~mask], np.asarray(material.stock)[~mask])
    assert Image.open(BytesIO(material.model_edit_target_png())).size == (640, 640)
    assert Image.open(BytesIO(material.edit_mask_png())).size == (640, 640)

def test_v4_contact_is_two_pixels_and_does_not_repaint_hat_or_glyph(material):
    artwork = Image.new("RGBA", (420, 160), (0, 0, 0, 0))
    ImageDraw.Draw(artwork).rectangle((30, 40, 390, 115), fill=(12, 55, 176, 255))
    v4 = build_slot1_material_pass(
        png(material.stock), png(artwork),
        {**DEFAULT_FRONT_PLACEMENT, "design_size": 100},
        contact_width_px=2)
    candidate = np.array(Image.open(BytesIO(refined_candidate(v4))), dtype=np.int16)
    glyph = np.asarray(v4.thread_mask) >= 128
    band = cv2.dilate(np.uint8(glyph), np.ones((5, 5), np.uint8)).astype(bool) & ~glyph
    candidate[band] -= 7
    final = np.asarray(Image.open(BytesIO(finish_slot1_refinement(
        v4, png(Image.fromarray(np.uint8(np.clip(candidate, 0, 255)), "RGB"))))))
    stock = np.asarray(v4.stock)
    permitted = np.asarray(v4.edit_mask) > 0
    contact = band & permitted
    assert np.any(np.any(final[contact] != stock[contact], axis=1))
    assert np.array_equal(final[~permitted], stock[~permitted])
    assert np.array_equal(final[350:385, 477:492], stock[350:385, 477:492])
    depression = stock[contact].astype(np.int16) - final[contact].astype(np.int16)
    assert np.max(depression) <= 8
    assert np.min(depression) >= 0


def test_v3_preserves_stock_outside_original_glyph(material):
    final = np.asarray(Image.open(BytesIO(
        finish_slot1_refinement(material, refined_candidate(material)))))
    no_glyph = np.asarray(material.thread_mask) == 0
    assert material.contact_width_px == 0
    assert np.array_equal(final[no_glyph], np.asarray(material.stock)[no_glyph])


def test_v4_rejects_wide_shadow_halo(material):
    art = Image.new("RGBA", (420, 160), (0, 0, 0, 0))
    ImageDraw.Draw(art).rectangle((30, 40, 390, 115), fill=(12, 55, 176, 255))
    v4 = build_slot1_material_pass(png(material.stock), png(art),
                                   DEFAULT_FRONT_PLACEMENT, contact_width_px=2)
    candidate = np.array(Image.open(BytesIO(refined_candidate(v4))), dtype=np.int16)
    glyph = np.asarray(v4.thread_mask) > 127
    near = cv2.dilate(np.uint8(glyph), np.ones((7, 7), np.uint8)).astype(bool) & ~glyph
    candidate[near] -= 65
    with pytest.raises(Slot1EmbroideryQAError, match="shadow halo"):
        finish_slot1_refinement(v4, png(Image.fromarray(
            np.uint8(np.clip(candidate, 0, 255)), "RGB")))


def test_lite_clips_ai_halo_then_checks_real_saved_pixels(material):
    art = Image.new("RGBA", (420, 160), (0, 0, 0, 0))
    ImageDraw.Draw(art).rectangle((30, 40, 390, 115), fill=(12, 55, 176, 255))
    lite = build_slot1_material_pass(
        png(material.stock), png(art), DEFAULT_FRONT_PLACEMENT, contact_width_px=2)
    candidate = np.array(Image.open(BytesIO(refined_candidate(lite))), dtype=np.int16)
    glyph = np.asarray(lite.thread_mask) > 127
    near = cv2.dilate(np.uint8(glyph), np.ones((7, 7), np.uint8)).astype(bool) & ~glyph
    candidate[near] -= 65
    edited = png(Image.fromarray(np.uint8(np.clip(candidate, 0, 255)), "RGB"))
    final = np.asarray(Image.open(BytesIO(
        finish_slot1_refinement(lite, edited, clip_raw_halo=True))))
    original = np.asarray(lite.stock)
    permitted = np.asarray(lite.edit_mask) > 0
    assert np.array_equal(final[~permitted], original[~permitted])
    contact = near & permitted & (np.asarray(lite.thread_mask) == 0)
    assert np.any(contact)
    depression = original[contact].astype(np.int16) - final[contact].astype(np.int16)
    assert np.max(depression) <= 8
    assert np.min(depression) >= 0
    assert np.any(final[glyph] != original[glyph])

def test_lite_localized_native_edit_retains_original_full_stock_and_contact_band(material):
    art = Image.new("RGBA", (420, 160), (0, 0, 0, 0))
    ImageDraw.Draw(art).rectangle((30, 40, 390, 115), fill=(12, 55, 176, 255))
    lite = build_slot1_material_pass(
        png(material.stock), png(art),
        {**DEFAULT_FRONT_PLACEMENT, "design_size": 100},
        contact_width_px=3, localized_edit=True,
    )
    assert lite.edit_roi_box is not None
    x0, y0, x1, y1 = lite.edit_roi_box
    a, b, c, d = lite.guide_box
    assert 0 <= x0 <= a < c <= x1 <= lite.stock.width
    assert 0 <= y0 <= b < d <= y1 <= lite.stock.height
    assert (x1-x0) < lite.stock.width and (y1-y0) < lite.stock.height
    target = Image.open(BytesIO(lite.model_edit_target_png()))
    protection = Image.open(BytesIO(lite.edit_mask_png()))
    assert target.size == protection.size == (512, 512)
    # The genuine original fabric is still visible in the crop, not a redraw.
    side = target.width
    px, py = (side-(x1-x0))//2, (side-(y1-y0))//2
    target_crop = np.asarray(target)[py:py+y1-y0, px:px+x1-x0]
    original_crop = np.asarray(lite.stock)[y0:y1, x0:x1]
    editable = np.asarray(lite.edit_mask)[y0:y1, x0:x1] > 0
    assert np.array_equal(target_crop[~editable], original_crop[~editable])
    output = finish_slot1_refinement(lite, refined_candidate(lite, square=True), clip_raw_halo=True)
    final = np.asarray(Image.open(BytesIO(output)))
    stock = np.asarray(lite.stock)
    full_mask = np.asarray(lite.edit_mask) > 0
    assert final.shape == stock.shape
    assert np.array_equal(final[~full_mask], stock[~full_mask])
    assert np.any(final[full_mask] != stock[full_mask])
    assert np.array_equal(final[350:385, 477:492], stock[350:385, 477:492])


def test_lite_roi_refuses_a_generated_whole_hat_instead_of_the_requested_crop(material):
    art = Image.new("RGBA", (420, 160), (0, 0, 0, 0))
    ImageDraw.Draw(art).rectangle((30, 40, 390, 115), fill=(12, 55, 176, 255))
    lite = build_slot1_material_pass(
        png(material.stock), png(art), DEFAULT_FRONT_PLACEMENT,
        contact_width_px=3, localized_edit=True,
    )
    # A non-square regenerated whole-hat frame violates the crop contract.
    with pytest.raises(Slot1EmbroideryQAError, match="aspect ratio"):
        finish_slot1_refinement(lite, refined_candidate(lite), clip_raw_halo=True)

def test_lite_rejects_random_noise_as_fake_thread_material(material):
    art = Image.new("RGBA", (420, 160), (0, 0, 0, 0))
    ImageDraw.Draw(art).rectangle((30, 40, 390, 115), fill=(12, 55, 176, 255))
    lite = build_slot1_material_pass(
        png(material.stock), png(art), DEFAULT_FRONT_PLACEMENT,
        contact_width_px=3, localized_edit=True,
    )
    base = np.array(Image.open(BytesIO(lite.model_edit_target_png())), dtype=np.int16)
    mask = np.array(Image.open(BytesIO(lite.edit_mask_png())), dtype=np.uint8) > 200
    noise = np.random.default_rng(123).normal(0, 13, mask.shape)
    base[mask] += np.rint(noise[mask])[:, None].astype(np.int16)
    bad = png(Image.fromarray(np.uint8(np.clip(base, 0, 255)), "RGB"))
    with pytest.raises(Slot1EmbroideryQAError, match="directional satin"):
        finish_slot1_refinement(lite, bad, clip_raw_halo=True)


def test_lite_rejects_coarse_fake_foam_ridges(material):
    art = Image.new("RGBA", (420, 160), (0, 0, 0, 0))
    ImageDraw.Draw(art).rectangle((30, 40, 390, 115), fill=(12, 55, 176, 255))
    lite = build_slot1_material_pass(
        png(material.stock), png(art), DEFAULT_FRONT_PLACEMENT,
        contact_width_px=3, localized_edit=True,
    )
    base = np.array(Image.open(BytesIO(lite.model_edit_target_png())), dtype=np.int16)
    mask = np.array(Image.open(BytesIO(lite.edit_mask_png())), dtype=np.uint8) > 200
    yy, xx = np.indices(mask.shape)
    bulge = 23 * np.sin((yy + xx * .05) * 2 * np.pi / 15)
    base[mask] += np.rint(bulge[mask])[:, None].astype(np.int16)
    bad = png(Image.fromarray(np.uint8(np.clip(base, 0, 255)), "RGB"))
    with pytest.raises(Slot1EmbroideryQAError, match="plastic-like stitch ridges"):
        finish_slot1_refinement(lite, bad, clip_raw_halo=True)
