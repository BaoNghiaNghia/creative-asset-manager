"""Physical embroidery is locked to one saved Slot 1 front master."""
from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.modules.image_generation.providers import GeneratedImageResult
from app.modules.realistic_review_ugc.embroidery_scale import (
    EmbroideryScaleError, angle_scale_prompt, front_scale_contract,
    front_transfer_box, pinned_master, projected_angle_box, saved_master_box,
)
from app.modules.realistic_review_ugc.angle_direct_edit import (
    angle_direct_prompt, process_direct_angle_edit,
)
from app.modules.realistic_review_ugc.slot1_embroidery import Slot1EmbroideryQAError, build_slot1_material_pass
from app.modules.realistic_review_ugc.threadtransfer import composite_reference_on_cap


def png(image):
    b = BytesIO()
    image.save(b, "PNG")
    return b.getvalue()


def material():
    image = Image.new("RGBA", (700, 500), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.rectangle((42, 165, 658, 275), fill=(216, 136, 22, 255))
    for x in range(44, 658, 3):
        d.line((x, 165, x, 275), fill=(236, 164, 35, 255), width=1)
    return png(image)


def source():
    image = Image.new("RGB", (900, 1000), (237, 221, 205))
    ImageDraw.Draw(image).text((730, 620), "GatorHats", fill=(24, 48, 79))
    return png(image)


def generator(image):
    return GeneratedImageResult(
        provider="codex", model="test", image_bytes=png(image),
        mime_type="image/png", provider_metadata={},
    )


def test_slot1_default_master_is_bigger_than_previous_height_limited_projection():
    contract = front_scale_contract(None)
    assert contract["version"] == "front-guide-v3"
    assert contract["guide_source"] == "saved_front_embroidery_area"
    box = front_transfer_box(contract, 2.6, (900, 1000))
    assert abs((box[0]+box[2]) / 2 / 900 - .5) < .006
    assert .61 < (box[2]-box[0])/900 < .62
    assert box[1] > 250 and box[3] < 620


def test_single_placement_master_projects_3_4_at_foreshortened_size():
    master = saved_master_box((255, 340, 615, 540), 900, 1000, "rev", "reference")
    b34 = projected_angle_box(master, "prod_s2_ec2", (900, 1000))
    b90 = projected_angle_box(master, "prod_s2_ec3", (900, 1000))
    assert .74 <= (b34[2]-b34[0])/(615-255) <= .81
    assert .45 < (b34[0]+b34[2])/2/900 < .51
    assert (b90[2]-b90[0]) < .16*(615-255)
    assert .28 < (b90[0]+b90[2])/2/900 < .35
    assert "PHYSICALLY LOCKED EMBROIDERY SIZE" in angle_scale_prompt(master,"prod_s2_ec2")
    assert "PHYSICALLY LOCKED EMBROIDERY SIZE" in angle_direct_prompt(
        "prod_s2_ec3", placement=master,
    )


def test_master_never_applies_to_different_reference_or_design_revision():
    master = saved_master_box((255, 340, 615, 540), 900, 1000, "rev", "reference-a")
    with pytest.raises(EmbroideryScaleError, match="different Embroidery Reference"):
        pinned_master(master, reference_remote_file_id="reference-b")
    with pytest.raises(EmbroideryScaleError, match="different source revision"):
        pinned_master(master, source_revision="different")


def test_slot1_composites_verified_thread_into_centered_master_and_saves_real_box():
    stock = source()
    art = Image.new("RGBA", (600, 420), (0, 0, 0, 0))
    ImageDraw.Draw(art).rectangle((50, 140, 550, 230), fill=(180, 90, 12, 255))
    stock_material = build_slot1_material_pass(
        stock, png(art), {}, localized_edit=True,
    )
    contract = front_scale_contract(None)
    out = composite_reference_on_cap(stock_material, material(),
                                     scale_contract=contract)
    box = out.target_box
    assert box == stock_material.thread_mask.getbbox()
    assert abs((box[0]+box[2])/2 / 900 - .5) < .01
    assert (box[2]-box[0])/900 > .32
    assert abs((box[1]+box[3])/2 / 1000 - contract["center_y"]) < .01
    master = saved_master_box(box, 900, 1000, "rev", "material")
    assert master["front_box"][0] >= .145
    assert master["front_box"][2] <= .855
    with Image.open(BytesIO(out.output_png)) as image:
        result = np.asarray(image.convert("RGB"))
    with Image.open(BytesIO(stock)) as image:
        original = np.asarray(image.convert("RGB"))
    assert np.array_equal(result[:, :120], original[:, :120])
    assert np.array_equal(result[700:], original[700:])


def test_new_slot1_rejects_macro_with_wrong_letter_proportions():
    stock = source()
    art = Image.new("RGBA", (600, 420), (0, 0, 0, 0))
    ImageDraw.Draw(art).rectangle((50, 140, 550, 230), fill=(180, 90, 12, 255))
    stock_material = build_slot1_material_pass(stock, png(art), {}, localized_edit=True)
    rotated_macro = Image.open(BytesIO(material())).transpose(Image.Transpose.ROTATE_90)
    with pytest.raises(Slot1EmbroideryQAError, match="proportions differ"):
        composite_reference_on_cap(stock_material, png(rotated_macro),
                                   scale_contract=front_scale_contract(None))


def test_3_4_oversized_off_center_native_edit_is_locally_normalized():
    stock = source()
    native = Image.open(BytesIO(stock)).convert("RGB")
    draw = ImageDraw.Draw(native)
    # The model incorrectly makes text ~70% of the visible face and shifts
    # right. Native thread pigment is still verified; stock remains immutable.
    # Separate glyph columns rather than a solid painted patch. Each letter
    # carries true visible fiber variation; a filled rectangle must still fail.
    for left in (275, 370, 465, 560, 655):
        draw.rectangle((left, 375, left+50, 510), fill=(206, 123, 18))
        for x in range(left+1, left+50, 3):
            draw.line((x, 375, x, 509), fill=(239, 164, 34), width=1)
    master = saved_master_box((255, 330, 620, 545), 900, 1000, "rev", "ref")
    out = process_direct_angle_edit(
        stock, generator(native), material(),
        stage="prod_s2_ec2", placement=master,
    )
    assert out.fallback_stock is False
    x0, y0, x1, y1 = out.edit_box
    expected = projected_angle_box(master, "prod_s2_ec2", (900, 1000))
    assert abs((x0+x1)-(expected[0]+expected[2])) <= 3
    assert (x1-x0) <= expected[2]-expected[0]+2
    assert (x1-x0) >= (expected[2]-expected[0])*.7
    result = np.asarray(Image.open(BytesIO(out.output_png)).convert("RGB"))
    original = np.asarray(Image.open(BytesIO(stock)).convert("RGB"))
    assert np.array_equal(result[:, :180], original[:, :180])
    assert np.array_equal(result[:, 690:], original[:, 690:])
    assert np.array_equal(result[680:], original[680:])


def test_side90_only_preserves_a_physically_calibrated_front_sliver():
    stock = source()
    native = Image.open(BytesIO(stock)).convert("RGB")
    draw = ImageDraw.Draw(native)
    draw.rectangle((255, 375, 288, 495), fill=(206, 123, 18))
    for x in range(256, 288, 3):
        draw.line((x, 375, x, 495), fill=(239, 164, 34), width=1)
    master = saved_master_box((255, 330, 620, 545), 900, 1000, "rev", "ref")
    out = process_direct_angle_edit(
        stock, generator(native), material(),
        stage="prod_s2_ec3", placement=master,
    )
    assert not out.fallback_stock
    assert out.edit_box[2]-out.edit_box[0] <= 65
    after = np.asarray(Image.open(BytesIO(out.output_png)).convert("RGB"))
    before = np.asarray(Image.open(BytesIO(stock)).convert("RGB"))
    assert np.array_equal(after[:, 500:], before[:, 500:])
