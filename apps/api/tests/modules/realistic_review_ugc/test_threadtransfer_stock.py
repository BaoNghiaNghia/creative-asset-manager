"""ThreadTransfer V2 angle QA: no camera redraw and no extra ImageGen calls."""
from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc.slot1_embroidery import Slot1EmbroideryQAError
from app.modules.realistic_review_ugc.threadtransfer_stock import (
    composite_reference_on_stock_view, _VIEW_CROWN,
)


def _png(im):
    output = BytesIO()
    im.save(output, "PNG")
    return output.getvalue()


def _reference():
    im = Image.new("RGBA",(700,600),(0,0,0,0))
    dr=ImageDraw.Draw(im)
    dr.rounded_rectangle((35,135,655,245),radius=12,fill=(25,42,132,255))
    dr.rounded_rectangle((140,300,600,350),radius=7,fill=(176,24,44,255))
    for x in range(36,654,3):
        dr.line((x,135,x,245),fill=(40,62,160,255),width=1)
    for x in range(140,600,3):
        dr.line((x,300,x,350),fill=(194,47,75,255),width=1)
    return _png(im)


@pytest.mark.parametrize("stage",["prod_s2_ec2","prod_s2_ec3"])
def test_angle_transfer_is_original_factory_photo_outside_tiny_crown_field(stage):
    stock=Image.new("RGB",(1200,1500),(229,215,199))
    dr=ImageDraw.Draw(stock)
    dr.line((120,100,1050,1300),fill=(75,89,117),width=6)
    # Side branding below the front-edge field is protected at every zoom.
    dr.text((930,790),"GatorHats",fill=(140,19,28))
    before=np.asarray(stock)
    finished=composite_reference_on_stock_view(_png(stock),_reference(),stage=stage)
    with Image.open(BytesIO(finished.output_png)) as image:
        after=np.asarray(image.convert("RGB"))
    left,top,right,bottom=(
        round(before.shape[1]*_VIEW_CROWN[stage][0]),
        round(before.shape[0]*_VIEW_CROWN[stage][1]),
        round(before.shape[1]*_VIEW_CROWN[stage][2]),
        round(before.shape[0]*_VIEW_CROWN[stage][3]),
    )
    assert np.array_equal(after[:top],before[:top])
    assert np.array_equal(after[bottom:],before[bottom:])
    assert np.array_equal(after[:,:left],before[:,:left])
    assert np.array_equal(after[:,right:],before[:,right:])
    assert (np.abs(after.astype(int)-before.astype(int)).sum()>1000)
    assert left<=finished.target_box[0]<finished.target_box[2]<=right
    assert top<=finished.target_box[1]<finished.target_box[3]<=bottom
    assert finished.extracted_pixels>=90
    assert len(finished.thread_layer_sha256)==64


def test_stock_side_must_not_stamp_full_front_slogan():
    stock=_png(Image.new("RGB",(1100,1400),(235,223,207)))
    front=composite_reference_on_stock_view(stock,_reference(),stage="prod_s2_ec2")
    side=composite_reference_on_stock_view(stock,_reference(),stage="prod_s2_ec3")
    full_box=front.target_box
    side_box=side.target_box
    assert (side_box[2]-side_box[0]) < (full_box[2]-full_box[0])*.33


def test_corrupt_or_blank_material_fails_closed():
    stock=_png(Image.new("RGB",(1000,1300),(230,220,205)))
    blank=_png(Image.new("RGB",(700,600),(230,220,205)))
    with pytest.raises(Slot1EmbroideryQAError,match="separated"):
        composite_reference_on_stock_view(stock,blank,stage="prod_s2_ec2")
    with pytest.raises(Slot1EmbroideryQAError,match="stock view"):
        composite_reference_on_stock_view(stock,_reference(),stage="prod_s2_ec4")
