from PIL import Image

from app.modules.realistic_review_ugc.output_image_sanitizer import (
    strip_black_matte_to_transparency,
)


def test_strips_only_connected_black_matte(tmp_path):
    path = tmp_path / "art.png"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 255))
    for y in range(14, 50):
        for x in range(14, 50):
            image.putpixel((x, y), (240, 30, 60, 255))
    image.putpixel((31, 31), (0, 0, 0, 255))
    image.save(path)
    assert strip_black_matte_to_transparency(path)
    with Image.open(path) as result:
        assert result.getpixel((0, 0))[3] == 0
        assert result.getpixel((31, 31)) == (0, 0, 0, 255)
        assert result.getpixel((20, 20)) == (240, 30, 60, 255)


def test_does_not_mutate_white_or_transparent_background(tmp_path):
    for color in ((255, 255, 255, 255), (0, 0, 0, 0)):
        path = tmp_path / ("art_%s.png" % color[3])
        Image.new("RGBA", (64, 64), color).save(path)
        before = path.read_bytes()
        assert not strip_black_matte_to_transparency(path)
        assert path.read_bytes() == before


def test_preserves_photograph_with_nonuniform_dark_border(tmp_path):
    path = tmp_path / "photo.png"
    image = Image.new("RGB", (64, 64), (0, 0, 0))
    image.putpixel((0, 0), (55, 55, 55))
    image.save(path)
    before = path.read_bytes()
    assert not strip_black_matte_to_transparency(path)
    assert path.read_bytes() == before


def test_repairs_opaque_blue_lettering_png_and_keeps_original_preview_bytes(tmp_path):
    from io import BytesIO
    from app.modules.realistic_review_ugc.output_image_sanitizer import (
        stage1_artwork_preview_bytes,
    )

    image = Image.new("RGB", (160, 96), (0, 0, 0))
    for y in range(15, 81):
        for x in range(28, 132):
            image.putpixel((x, y), (35, 80, 180))
    # Preserve internal black holes, not connected to the background.
    image.putpixel((60, 30), (0, 0, 0))
    original_buffer = BytesIO()
    image.save(original_buffer, format="PNG")
    original_bytes = original_buffer.getvalue()

    converted_bytes, changed = stage1_artwork_preview_bytes(original_bytes)
    assert changed
    assert converted_bytes != original_bytes
    with Image.open(BytesIO(converted_bytes)) as repaired:
        assert repaired.mode == "RGBA"
        assert repaired.getpixel((0, 0))[3] == 0
        assert repaired.getpixel((60, 30)) == (0, 0, 0, 255)
        assert repaired.getpixel((44, 44)) == (35, 80, 180, 255)

    source = tmp_path / "original.png"
    source.write_bytes(original_bytes)
    assert source.read_bytes() == original_bytes, "preview must never overwrite original"


def test_old_jpeg_thumbnail_black_matte_becomes_png_with_alpha():
    from io import BytesIO
    from app.modules.realistic_review_ugc.output_image_sanitizer import (
        stage1_artwork_preview_bytes,
    )

    image = Image.new("RGB", (180, 120), (0, 0, 0))
    for y in range(18, 100):
        for x in range(28, 150):
            image.putpixel((x, y), (30, 74, 170))
    blob = BytesIO()
    image.save(blob, format="JPEG", quality=95)
    original_bytes = blob.getvalue()
    converted, changed = stage1_artwork_preview_bytes(original_bytes)
    assert changed
    assert converted.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(converted)) as img:
        assert img.getpixel((0, 0))[3] == 0
        assert img.getpixel((68, 50))[3] == 255


def test_preview_never_changes_true_alpha_photos_invalid_or_large_images():
    from io import BytesIO
    from app.modules.realistic_review_ugc.output_image_sanitizer import (
        MAX_PREVIEW_BYTES, stage1_artwork_preview_bytes,
    )
    for color in ((255, 255, 255, 255), (0, 0, 0, 0), (0, 0, 0, 255)):
        data = BytesIO()
        Image.new("RGBA", (80, 80), color).save(data, format="PNG")
        blob = data.getvalue()
        assert stage1_artwork_preview_bytes(blob) == (blob, False)
    blob = b"not-an-image"
    assert stage1_artwork_preview_bytes(blob) == (blob, False)
    oversized = b"x" * (MAX_PREVIEW_BYTES + 1)
    assert stage1_artwork_preview_bytes(oversized) == (oversized, False)


def test_preview_dark_photo_with_nonuniform_border_is_unchanged():
    from io import BytesIO
    from app.modules.realistic_review_ugc.output_image_sanitizer import (
        stage1_artwork_preview_bytes,
    )
    image = Image.new("RGB", (90, 90), (8, 8, 8))
    # A genuine dark photo has a visibly nonuniform border on multiple sides.
    for y in range(0, 45):
        image.putpixel((0, y), (63, 62, 64))
    for x in range(0, 45):
        image.putpixel((x, 0), (71, 73, 75))
    data = BytesIO()
    image.save(data, format="PNG")
    original = data.getvalue()
    assert stage1_artwork_preview_bytes(original) == (original, False)
