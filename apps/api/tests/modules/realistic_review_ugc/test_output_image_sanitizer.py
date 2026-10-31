"""Stage 1 source-PNG thumbnails preserve alpha, ratio and original pixels."""
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc.output_image_sanitizer import (
    MAX_PREVIEW_BYTES, stage1_png_thumbnail_bytes, stage1_webp_thumbnail_bytes,
)


def sample_png(mode="RGBA"):
    image = Image.new(mode, (2048, 1024), (0, 0, 0, 0) if mode == "RGBA" else (0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((320, 200, 1720, 800), fill=(36, 80, 176, 255) if mode == "RGBA" else (36, 80, 176))
    draw.rectangle((500, 410, 640, 550), fill=(0, 0, 0, 255) if mode == "RGBA" else (0, 0, 0))
    data = BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def test_original_rgba_png_preserves_transparency_blue_and_black_stitch():
    original = sample_png()
    resized = stage1_png_thumbnail_bytes(original, 400)
    assert resized.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(resized)) as result:
        assert result.mode == "RGBA"
        assert result.size == (400, 200)  # Do not crop 2:1 to a square
        assert result.getpixel((0, 0))[3] == 0
        assert result.getpixel((100, 70)) == (36, 80, 176, 255)
        assert result.getpixel((110, 85)) == (0, 0, 0, 255)


def test_opaque_black_pixels_remain_opaque_and_are_never_reinterpreted_as_alpha():
    original = sample_png("RGB")
    resized = stage1_png_thumbnail_bytes(original, 256)
    with Image.open(BytesIO(resized)) as result:
        assert result.size == (256, 128)
        assert result.getpixel((0, 0)) == (0, 0, 0)
        assert result.getpixel((70, 60)) == (0, 0, 0)


def test_png_palette_transparency_survives():
    image = Image.new("P", (300, 150), 0)
    image.putpalette([0, 0, 0, 40, 72, 160] + [0] * 762)
    image.info["transparency"] = 0
    ImageDraw.Draw(image).rectangle((50, 25, 250, 125), fill=1)
    data = BytesIO()
    image.save(data, format="PNG")
    with Image.open(BytesIO(stage1_png_thumbnail_bytes(data.getvalue(), 300))) as resized:
        assert resized.mode == "RGBA"
        assert resized.getpixel((0, 0))[3] == 0
        assert resized.getpixel((100, 70)) == (40, 72, 160, 255)


def test_invalid_and_oversized_inputs_fail_without_fabricating_preview():
    for image in (b"", b"not-a-png", b"0" * (MAX_PREVIEW_BYTES + 1)):
        with pytest.raises(ValueError):
            stage1_png_thumbnail_bytes(image, 256)
    with pytest.raises(ValueError):
        stage1_png_thumbnail_bytes(sample_png(), 2048)
    not_png = BytesIO()
    Image.new("RGB", (50, 30), "black").save(not_png, "JPEG")
    with pytest.raises(ValueError):
        stage1_png_thumbnail_bytes(not_png.getvalue(), 256)

def test_webp_thumbnail_is_small_keeps_alpha_and_original_ratio():
    original = sample_png()
    compact = stage1_webp_thumbnail_bytes(original, 256)
    assert len(compact) < len(stage1_png_thumbnail_bytes(original, 256))
    with Image.open(BytesIO(compact)) as image:
        assert image.format == "WEBP"
        assert image.size == (256, 128)
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((50, 35))[3] == 255
        # Lossy thumbnails may deviate slightly; original download remains exact.
        color = image.getpixel((50, 35))
        assert abs(color[0] - 36) < 25
        assert abs(color[1] - 80) < 25
        assert abs(color[2] - 176) < 25


def test_webp_thumbnail_never_makes_opaque_black_transparent():
    original = sample_png("RGB")
    compact = stage1_webp_thumbnail_bytes(original, 256)
    with Image.open(BytesIO(compact)) as image:
        assert image.format == "WEBP"
        assert image.mode == "RGB"
        assert image.size == (256, 128)
        assert image.getpixel((0, 0)) == (0, 0, 0)
