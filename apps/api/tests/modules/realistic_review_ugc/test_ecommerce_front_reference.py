"""QA for deterministic, calibrated front embroidery input assets."""
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc.ecommerce_front_reference import (
    FRONT_GUIDE, FrontReferenceError, render_front_reference,
)


def _bytes(image: Image.Image, fmt: str) -> bytes:
    output = BytesIO()
    image.save(output, format=fmt)
    return output.getvalue()


def test_front_reference_preserves_stock_outside_front_area_and_trims_padding():
    stock = Image.new("RGB", (512, 640), (211, 197, 158))
    stock_bytes = _bytes(stock, "PNG")
    artwork = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    ImageDraw.Draw(artwork).rectangle((145, 75, 255, 125), fill=(165, 13, 18, 255))
    result = render_front_reference(stock_bytes, _bytes(artwork, "PNG"))
    with Image.open(BytesIO(result)) as image:
        assert image.format == "PNG"
        assert image.size == (512, 640)
        assert image.getpixel((0, 0)) == (211, 197, 158)
        assert image.getpixel((256, 100)) == (211, 197, 158)
        assert image.getpixel((256, 230)) == (165, 13, 18)
        x, y, w, h = FRONT_GUIDE
        approved = (round(x*512), round(y*640), round((x+w)*512), round((y+h)*640))
        changed = Image.new("RGB", stock.size)
        changed.paste(image)
        mask = changed.copy()
        # All non-background pixels belong inside the calibrated embroidery field.
        pixels = [pos for yy in range(640) for xx in range(512)
                  if (pos := (xx, yy)) and image.getpixel(pos) != (211, 197, 158)]
        assert pixels
        assert all(approved[0] <= xx < approved[2] and approved[1] <= yy < approved[3]
                   for xx, yy in pixels)


def test_opaque_white_artwork_background_is_removed():
    stock = _bytes(Image.new("RGB", (512, 640), (177, 166, 142)), "JPEG")
    image = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(image).rectangle((30, 28, 169, 71), fill="blue")
    result = render_front_reference(stock, _bytes(image, "PNG"))
    with Image.open(BytesIO(result)) as preview:
        assert preview.size == (512, 640)
        assert preview.getpixel((256, 231))[2] > 180


def test_blank_artwork_fails_closed():
    stock = _bytes(Image.new("RGB", (512, 640), "white"), "PNG")
    with pytest.raises(FrontReferenceError, match="no visible pixels"):
        render_front_reference(stock, _bytes(Image.new("RGB", (200, 100), "white"), "PNG"))
