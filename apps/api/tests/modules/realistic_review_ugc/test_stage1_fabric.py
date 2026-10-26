from io import BytesIO
from PIL import Image, ImageDraw
from app.modules.realistic_review_ugc.stage1_fabric import render_fabric_reference, fabric_output_bytes, extract_thread_geometry, FABRIC_SHA_KEY
from app.modules.realistic_review_ugc.ecommerce_front_reference import render_front_reference


def test_fabric_first_opaque_reference_preserves_text_not_cloth():
    art = Image.new("RGBA", (480, 240), (0, 0, 0, 0))
    ImageDraw.Draw(art).rectangle((100, 65, 380, 170), fill="#2852AA")
    fabric = render_fabric_reference(art)
    assert fabric.mode == "RGB"
    assert fabric.getpixel((5, 5))[0] > 160
    assert fabric.getpixel((180, 100))[2] > fabric.getpixel((180, 100))[0]
    b = BytesIO(fabric_output_bytes(art))
    with Image.open(BytesIO(b.getvalue())) as saved:
        assert saved.mode == "RGB"
        assert extract_thread_geometry(saved).getchannel("A").tobytes() == art.getchannel("A").tobytes()
    stock = Image.new("RGB", (1000, 1000), (18, 80, 49))
    stock_buf = BytesIO()
    stock.save(stock_buf, format="PNG")
    result = render_front_reference(stock_buf.getvalue(), b.getvalue())
    img = Image.open(BytesIO(result)).convert("RGB")
    # No cream fabric rectangle may appear anywhere on the green hat.
    pixels = img.get_flattened_data()
    assert not any(r > 160 and g > 145 and b > 130 for r, g, b in pixels)
    assert any(b > r * 1.3 for r, g, b in pixels)

def test_thread_preview_is_ephemeral_and_preserves_legacy_master():
    from app.modules.realistic_review_ugc.stage1_fabric import thread_preview_bytes
    import pytest
    art = Image.new("RGBA", (480, 240), (0, 0, 0, 0))
    ImageDraw.Draw(art).rectangle((90, 65, 390, 170), fill="#2852AA")
    opaque = fabric_output_bytes(art)
    with Image.open(BytesIO(opaque)) as saved:
        assert saved.mode == "RGB"
    with Image.open(BytesIO(thread_preview_bytes(opaque, 192))) as thread:
        assert thread.mode == "RGBA"
        assert thread.size == (192, 96)
        assert thread.getpixel((0, 0))[3] == 0
    legacy = BytesIO()
    art.save(legacy, format="PNG")
    with Image.open(BytesIO(thread_preview_bytes(legacy.getvalue()))) as result:
        assert result.mode == "RGBA"
        assert result.getchannel("A").tobytes() == art.getchannel("A").tobytes()
    unverified = BytesIO()
    Image.new("RGB", (480, 240), (225, 220, 211)).save(unverified, format="PNG")
    with pytest.raises(ValueError, match="lacks verified"):
        thread_preview_bytes(unverified.getvalue())


def test_untagged_textured_photo_refuses_unsafe_patch_transfer():
    from app.modules.realistic_review_ugc.ecommerce_front_reference import FrontReferenceError
    import pytest
    stock = Image.new("RGB", (512, 640), (37, 61, 82))
    source = Image.new("RGB", (480, 240), (220, 216, 209))
    from app.modules.realistic_review_ugc.stage1_photo_samples import reference_patch
    patch = reference_patch("fabric").convert("RGB")
    for y in range(0, source.height, 64):
        for x in range(0, source.width, 64):
            source.paste(patch, (x, y))
    stock_buf, source_buf = BytesIO(), BytesIO()
    stock.save(stock_buf, format="PNG")
    source.save(source_buf, format="PNG")
    with pytest.raises(FrontReferenceError, match="verified source"):
        render_front_reference(stock_buf.getvalue(), source_buf.getvalue())


def test_fabric_geometry_checksum_rejects_tampering():
    from pytest import raises
    art = Image.new("RGBA", (160, 90), (0, 0, 0, 0))
    ImageDraw.Draw(art).rectangle((22, 21, 111, 74), fill="blue")
    with Image.open(BytesIO(fabric_output_bytes(art))) as tagged:
        tagged.load()
        assert extract_thread_geometry(tagged) is not None
        tagged.info[FABRIC_SHA_KEY] = "0" * 64
        with raises(ValueError, match="Corrupted"):
            extract_thread_geometry(tagged)
