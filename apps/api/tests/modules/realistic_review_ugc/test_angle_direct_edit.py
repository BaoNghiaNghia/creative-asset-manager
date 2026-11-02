"""Single-pass Codex angle QA does not publish rebuilt caps or patch rectangles."""
from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.modules.image_generation.providers import GeneratedImageResult
from app.modules.realistic_review_ugc.angle_direct_edit import (
    AngleDirectEditError, angle_direct_prompt, process_direct_angle_edit,
    _FRONT_CROWN,
)


def png(im):
    b = BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def reference():
    image = Image.new("RGBA", (720, 620), (0,0,0,0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70,160,650,285), radius=16, fill=(187,59,12,255))
    draw.rounded_rectangle((120,320,620,398), radius=16, fill=(225,147,17,255))
    for x in range(71,650,3):
        draw.line((x,162,x,283), fill=(215,86,30,255), width=1)
    for x in range(122,620,3):
        draw.line((x,321,x,398), fill=(248,168,35,255), width=1)
    return png(image)


def stock():
    im = Image.new("RGB",(900,1000),(225,212,195))
    d = ImageDraw.Draw(im)
    d.rectangle((0,0,900,230), fill=(255,255,255))
    d.polygon([(0,850),(600,700),(800,900),(0,1000)], fill=(24,45,70))
    d.text((700,620), "GatorHats", fill=(18,42,72))
    return im


def candidate(stage, *, location="correct", patch=False, flat=False):
    im = stock()
    d = ImageDraw.Draw(im)
    x0,y0,x1,y1=_FRONT_CROWN[stage]
    if location=="wrong":
        x,y=round(im.width*.70),round(im.height*.39)
    else:
        x,y=round(im.width*(x0+.04)),round(im.height*(y0+.07))
    if patch:
        d.rectangle((round(x0*900),round(y0*1000),
                     round(x1*900),round(y1*1000)),fill=(187,59,12))
    else:
        wide = 200 if stage=="prod_s2_ec2" else 28
        d.rectangle((x,y,x+wide,y+80),fill=(187,59,12))
        if not flat:
            for xx in range(x,x+wide,3):
                d.line((xx,y,xx,y+79),fill=(215,86,30),width=1)
    return png(im)


def gen(data):
    return GeneratedImageResult(provider="codex", model="test",
                                image_bytes=data,mime_type="image/png",
                                provider_metadata={})


def test_direct_prompts_lock_stock_and_side_geometry():
    front = angle_direct_prompt("prod_s2_ec2")
    side = angle_direct_prompt("prod_s2_ec3")
    for text in (front,side):
        assert "ONE IMAGE-EDIT invocation only" in text
        assert "REFERENCE B" in text
        assert "REFERENCE C" in text
        assert "No mask image" in text
        assert "GatorHats side logo" in text
    assert "tiny" in side.casefold()
    assert "visor points LEFT" in side
    assert "FRONT THREE-QUARTER" in front


def test_three_quarter_only_accepts_stitch_pixels_preserving_every_other_stock_pixel():
    original=png(stock())
    result=process_direct_angle_edit(original,gen(candidate("prod_s2_ec2")),
                                     reference(),stage="prod_s2_ec2")
    assert not result.fallback_stock
    assert result.accepted_pixels>160
    assert len(result.native_sha256)==64
    after=np.asarray(Image.open(BytesIO(result.output_png)).convert("RGB"))
    before=np.asarray(Image.open(BytesIO(original)).convert("RGB"))
    assert np.count_nonzero(np.any(after!=before,axis=2))>100
    assert np.array_equal(after[:250],before[:250])
    assert np.array_equal(after[:,650:],before[:,650:])
    # Branding cannot be re-rendered or erased.
    assert np.array_equal(after[610:680,690:890],before[610:680,690:890])


def test_multicolor_lower_line_is_transferred_in_three_quarter_without_patch():
    # A real ORGY REFEREE-like reference has two pigments. The older three
    # luminance medians could collapse them, and the old crown ended at 56%,
    # clipping this second line that sits above the visor.
    original = png(stock())
    ref = Image.new("RGBA", (720, 620), (0, 0, 0, 0))
    paint = ImageDraw.Draw(ref)
    for top, low, high in (
        (125, (190, 65, 16, 255), (226, 99, 40, 255)),
        (305, (10, 93, 104, 255), (25, 133, 139, 255)),
    ):
        paint.rectangle((70, top, 630, top+120), fill=low)
        for x in range(70, 630, 3):
            paint.line((x, top, x, top+119), fill=high, width=1)
    edited = stock()
    paint = ImageDraw.Draw(edited)
    for top, low, high in (
        (370, (190, 65, 16), (226, 99, 40)),
        (575, (10, 93, 104), (25, 133, 139)),
    ):
        paint.rectangle((255, top, 540, top+54), fill=low)
        for x in range(255, 540, 3):
            paint.line((x, top, x, top+53), fill=high, width=1)
    outcome = process_direct_angle_edit(
        original, gen(png(edited)), png(ref), stage="prod_s2_ec2",
    )
    result = np.asarray(Image.open(BytesIO(outcome.output_png)).convert("RGB"))
    baseline = np.asarray(Image.open(BytesIO(original)).convert("RGB"))
    assert outcome.accepted_pixels > 1200
    assert np.count_nonzero(np.any(result[370:420, 255:540] != baseline[370:420, 255:540], axis=2)) > 400
    assert np.count_nonzero(np.any(result[575:625, 255:540] != baseline[575:625, 255:540], axis=2)) > 400
    assert np.array_equal(result[:, 700:], baseline[:, 700:])
    assert np.array_equal(result[700:], baseline[700:])


def test_three_quarter_rejects_rectangular_decal():
    with pytest.raises(AngleDirectEditError,match="patch"):
        process_direct_angle_edit(png(stock()),gen(candidate("prod_s2_ec2",patch=True)),
                                  reference(),stage="prod_s2_ec2")


def test_side_wrong_rear_letters_fallback_to_factory_stock():
    original=png(stock())
    result=process_direct_angle_edit(original,gen(candidate("prod_s2_ec3",location="wrong")),
                                     reference(),stage="prod_s2_ec3")
    assert result.fallback_stock
    assert result.accepted_pixels==0
    assert np.array_equal(
        np.asarray(Image.open(BytesIO(result.output_png)).convert("RGB")),
        np.asarray(Image.open(BytesIO(original)).convert("RGB")),
    )


def test_side_tiny_visor_edge_can_pass_without_side_brand_modification():
    original=png(stock())
    result=process_direct_angle_edit(original,gen(candidate("prod_s2_ec3")),
                                     reference(),stage="prod_s2_ec3")
    assert not result.fallback_stock
    after=np.asarray(Image.open(BytesIO(result.output_png)).convert("RGB"))
    before=np.asarray(Image.open(BytesIO(original)).convert("RGB"))
    assert np.array_equal(after[610:680,690:890],before[610:680,690:890])


def test_side_full_slogan_in_front_area_is_not_accepted():
    original = png(stock())
    image = stock()
    d = ImageDraw.Draw(image)
    d.rectangle((230,345,410,425), fill=(187,59,12))
    for x in range(230,410,3):
        d.line((x,345,x,424), fill=(215,86,30), width=1)
    validated = process_direct_angle_edit(
        original, gen(png(image)), reference(), stage="prod_s2_ec3",
    )
    assert validated.fallback_stock
    assert any(label in (validated.fallback_reason or "")
               for label in ("full slogan", "filled patch"))
    assert np.array_equal(
        np.asarray(Image.open(BytesIO(validated.output_png)).convert("RGB")),
        np.asarray(Image.open(BytesIO(original)).convert("RGB")),
    )


def test_side_flat_or_noop_returns_stock_while_three_quarter_fails():
    original=png(stock())
    side=process_direct_angle_edit(original,gen(original),reference(),stage="prod_s2_ec3")
    assert side.fallback_stock
    with pytest.raises(AngleDirectEditError,match="No credible"):
        process_direct_angle_edit(original,gen(original),reference(),stage="prod_s2_ec2")


def test_reject_mismatch_dimensions_and_native_sha():
    wrong=png(Image.new("RGB",(1050,1000),(224,215,195)))
    with pytest.raises(AngleDirectEditError,match="frame dimensions"):
        process_direct_angle_edit(png(stock()),gen(wrong),reference(),stage="prod_s2_ec2")
    generated=gen(candidate("prod_s2_ec2"))
    generated.provider_metadata["native_sha256"]="f"*64
    with pytest.raises(AngleDirectEditError,match="provenance"):
        process_direct_angle_edit(png(stock()),generated,reference(),stage="prod_s2_ec2")
