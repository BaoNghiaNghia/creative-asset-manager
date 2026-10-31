"""Typography preview tiles keep originals immutable and respect alpha/aspect."""
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc.typography_thumbnails import typography_thumbnail_webp


def image(mode="RGBA"):
    sample = Image.new(mode, (1200, 600), (0, 0, 0, 0) if mode == "RGBA" else (0, 0, 0))
    ImageDraw.Draw(sample).rectangle(
        (180, 120, 1020, 470),
        fill=(34, 77, 190, 255) if mode == "RGBA" else (34, 77, 190),
    )
    out = BytesIO()
    sample.save(out, "PNG")
    return out.getvalue()


def test_typography_tile_webp_preserves_ratio_and_rgba_without_upscaling():
    original = image()
    compressed = typography_thumbnail_webp(original, 320)
    assert compressed != original
    with Image.open(BytesIO(compressed)) as preview:
        assert preview.format == "WEBP"
        assert preview.size == (320, 160)
        assert preview.mode == "RGBA"
        assert preview.getpixel((0, 0))[3] == 0
        assert preview.getpixel((150, 65))[3] == 255
    with Image.open(BytesIO(original)) as original_image:
        assert original_image.size == (1200, 600)


def test_typography_tile_opaque_black_stays_opaque():
    with Image.open(BytesIO(typography_thumbnail_webp(image("RGB"), 256))) as preview:
        assert preview.mode == "RGB"
        assert preview.getpixel((0, 0)) == (0, 0, 0)
        assert preview.size == (256, 128)


def test_typography_tile_limits_are_fail_closed():
    for value in (0, 127, 641, 100000):
        with pytest.raises(ValueError):
            typography_thumbnail_webp(image(), value)
    with pytest.raises(ValueError):
        typography_thumbnail_webp(b"invalid image", 320)
