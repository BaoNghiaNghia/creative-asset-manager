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
