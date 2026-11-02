"""ThreadTransfer V1 photo compositing, alpha integrity, and fail-closed QA."""
from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc.slot1_embroidery import (
    Slot1EmbroideryQAError, build_slot1_material_pass,
)
from app.modules.realistic_review_ugc.threadtransfer import (
    _trim_chromatic_gutters, composite_reference_on_cap,
)


def png(image: Image.Image) -> bytes:
    out = BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def stock_and_design():
    stock = Image.new("RGB", (800, 1000), (229, 218, 203))
    # Original details outside crown area may never change.
    draw = ImageDraw.Draw(stock)
    draw.line((400, 0, 400, 230), fill=(110, 99, 88), width=3)
    draw.rectangle((35, 655, 760, 920), fill=(31, 48, 77))
    artwork = Image.new("RGBA", (650, 450), (0, 0, 0, 0))
    draw = ImageDraw.Draw(artwork)
    draw.rectangle((65, 95, 580, 160), fill=(12, 45, 128, 255))
    draw.rectangle((140, 210, 510, 258), fill=(150, 25, 35, 255))
    material = build_slot1_material_pass(
        png(stock), png(artwork), {}, contact_width_px=3, localized_edit=True,
    )
    return stock, material


def photographed_stitches(*, transparent: bool = False):
    w, h = 700, 600
    rng = np.random.default_rng(135)
    cloth_noise = rng.normal(0, 1.8, (h, w, 1))
    rgb = np.uint8(np.clip(
        np.array([235, 220, 201])[None, None, :] + cloth_noise, 0, 255,
    ))
    mask_image = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask_image)
    d.rounded_rectangle((70, 120, 625, 220), radius=12, fill=255)
    d.rounded_rectangle((165, 270, 545, 355), radius=10, fill=255)
    mask = np.asarray(mask_image) > 0
    stripe = np.sin(np.arange(w, dtype=np.float32) * 2.4)[None, :, None] * 10
    fine = rng.normal(0, 3.4, (h, w, 1))
    thread = np.clip(
        np.array([13, 42, 150])[None, None, :] + stripe + fine, 0, 255,
    )
    rgb[mask] = np.uint8(thread[mask])
    if transparent:
        rgba = np.dstack((rgb, np.uint8(mask) * 255))
        return png(Image.fromarray(rgba, "RGBA"))
    return png(Image.fromarray(rgb, "RGB"))


@pytest.mark.parametrize("transparent", [False, True])
def test_reference_transfer_preserves_original_cap_outside_pinned_field(transparent):
    stock, material = stock_and_design()
    reference = photographed_stitches(transparent=transparent)
    result = composite_reference_on_cap(material, reference, design_size=100)
    assert result.output_png.startswith(b"\\x89PNG".decode("unicode_escape").encode("latin1"))
    assert len(result.thread_layer_sha256) == 64
    assert result.extracted_pixels > 1000
    with Image.open(BytesIO(result.output_png)) as converted:
        output = np.asarray(converted.convert("RGB"))
    original = np.asarray(stock)
    x0, y0, x1, y1 = material.guide_box
    assert output.shape == original.shape
    assert np.array_equal(output[:y0], original[:y0])
    assert np.array_equal(output[y1:], original[y1:])
    assert np.array_equal(output[:, :x0], original[:, :x0])
    assert np.array_equal(output[:, x1:], original[:, x1:])
    assert np.count_nonzero(np.any(output[y0:y1, x0:x1] != original[y0:y1, x0:x1], axis=2)) > 300
    px0, py0, px1, py1 = result.target_box
    assert x0 <= px0 < px1 <= x1 and y0 <= py0 < py1 <= y1


def test_transparent_reference_does_not_transfer_backing_cloth():
    stock, material = stock_and_design()
    out = composite_reference_on_cap(material, photographed_stitches(transparent=True))
    with Image.open(BytesIO(out.output_png)) as image:
        frame = np.asarray(image.convert("RGB"))
    x0, y0, x1, y1 = out.target_box
    # Transparent corners of the reference leave the original cap untouched.
    src = np.asarray(stock)
    assert np.array_equal(frame[y0 + 2, x0 + 2], src[y0 + 2, x0 + 2])


def test_dominant_colored_stitches_trim_faint_false_positive_gutters():
    source = np.zeros((650, 1400, 4), dtype=np.uint8)
    for x in range(12, 1395, 34):
        source[15:630, x:x+3] = (126, 120, 113, 90)
    source[158:515, 282:1118] = (25, 67, 181, 255)
    source[175:500:3, 282:1118, :3] = (38, 86, 205)
    layer = Image.fromarray(source, "RGBA")
    cleaned = _trim_chromatic_gutters(layer)
    assert 830 <= cleaned.width < 950
    assert 350 <= cleaned.height < 420
    assert np.asarray(cleaned.getchannel("A")).max() == 255
    # Monochrome embroidery is not cropped using another word's chroma.
    mono = np.array(source)
    mono[158:515, 282:1118, :3] = (50, 50, 50)
    assert _trim_chromatic_gutters(Image.fromarray(mono, "RGBA")).size == layer.size


def test_uniform_fabric_only_and_flat_print_fail_without_publishing():
    _, material = stock_and_design()
    flat_cloth = png(Image.new("RGB", (600, 600), (235, 220, 201)))
    with pytest.raises(Slot1EmbroideryQAError, match="separated"):
        composite_reference_on_cap(material, flat_cloth)
    flat_art = Image.new("RGB", (600, 600), (235, 220, 201))
    ImageDraw.Draw(flat_art).rectangle((100, 230, 500, 340), fill=(12, 45, 140))
    with pytest.raises(Slot1EmbroideryQAError, match="thread texture"):
        composite_reference_on_cap(material, png(flat_art))
