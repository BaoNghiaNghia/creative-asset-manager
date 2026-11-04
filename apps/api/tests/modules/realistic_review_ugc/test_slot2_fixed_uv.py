"""Fixed shared UV profile: geometry, alpha/occlusion and real PNG determinism."""
from __future__ import annotations

import hashlib
from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc.slot2_fixed_uv import (
    PIPELINE_VERSION, FixedUvProjectionError, load_profile,
    render_fixed_uv_slot2, _shared_texture, _input_design, _project_atlas,
)


def design_sample(*, flip=False):
    artwork = Image.new("RGBA", (420, 230), (0, 0, 0, 0))
    draw = ImageDraw.Draw(artwork)
    draw.rounded_rectangle((40, 55, 380, 170), radius=12, fill=(207, 32, 58, 255))
    draw.rectangle((135 if not flip else 280, 80, 165 if not flip else 310, 148),
                   fill=(23, 190, 87, 255))
    out = BytesIO()
    artwork.save(out, "PNG")
    return out.getvalue()


def stock_sample(size=(1024, 1024)):
    photo = Image.new("RGB", size, (238, 225, 212))
    out = BytesIO()
    photo.save(out, "PNG")
    return out.getvalue()


def test_frozen_user_hat_grid_nodes_and_rules():
    profile, sha = load_profile()
    assert PIPELINE_VERSION == "fixed-shared-uv-v1"
    assert len(sha) == 64
    assert profile["calibrationStatus"] == "estimated-from-reference-images-not-camera-calibrated"
    assert profile["views"]["front"]["nodes"][2][2] == [198, 300]
    assert profile["views"]["threeQuarter"]["nodes"][2][2] == [455.948967, 521.164319]
    assert profile["views"]["threeQuarter"]["nodes"][4][4] == [681.832553, 730.188604]
    assert profile["placementDefaults"]["centerU"] == .5
    assert profile["placementDefaults"]["centerV"] == .5
    assert profile["rules"]["extraRotationDegrees"] == 0
    assert profile["rules"]["extraDomeWarp"] is False
    assert profile["rules"]["autoRecoverHiddenArtwork"] is False
    assert "C338,620" in profile["views"]["threeQuarter"]["visibleMaskPath"]


def test_one_texture_has_center_and_aspect_locked_independent_of_camera_view():
    data = design_sample()
    profile, _ = load_profile()
    atlas, box = _shared_texture(_input_design(data), profile["logicalArea"])
    assert atlas.shape == (round(1024*150/268), 1024, 4)
    assert abs((box[0]+box[2])/2-.5) < 1e-12
    assert abs((box[1]+box[3])/2-.5) < 1e-12
    assert abs(((box[2]-box[0])*268)/((box[3]-box[1])*150)-341/116) < .02
    front = _project_atlas(atlas, profile["views"]["front"], (399, 598))
    angle = _project_atlas(atlas, profile["views"]["threeQuarter"], (1254, 1254))
    assert np.count_nonzero(front[..., 3] > 200) > 100
    assert np.count_nonzero(angle[..., 3] > 200) > 100
    # One common atlas, one palette and two fixed screen mappings.
    assert front[..., 0].max() > 180
    assert angle[..., 0].max() > 180


def test_clean_slot2_png_clips_invisible_yarn_without_modifying_factory_stock():
    original = stock_sample()
    design = design_sample()
    _, sha = load_profile()
    result = render_fixed_uv_slot2(design, original, expected_profile_sha256=sha)
    assert hashlib.sha256(design).hexdigest() == result.design_sha256
    assert result.profile_sha256 == sha
    assert result.visible_pixel_count > 1000
    assert 0 < result.visible_fraction <= 1
    assert len(result.atlas_sha256) == 64
    assert result.front_anchor == pytest.approx((198/399, 300/598))
    assert result.three_quarter_anchor == pytest.approx((455.948967/1254, 521.164319/1254))
    assert result.uv_box[0] + result.uv_box[2] == pytest.approx(1)
    assert result.uv_box[1] + result.uv_box[3] == pytest.approx(1)
    output = Image.open(BytesIO(result.output_png))
    assert output.format == "PNG"
    assert output.size == (1024, 1024)
    arr = np.asarray(output)
    assert arr[40, 40].tolist() == [238, 225, 212]
    assert arr[950, 950].tolist() == [238, 225, 212]
    assert np.any(np.all(arr[..., :3] != [238, 225, 212], axis=2))
    # Front audit image is a transparent projected layer, not Slot 1 replacement.
    front = Image.open(BytesIO(result.front_layer_png))
    assert front.mode == "RGBA"
    assert front.size == (399, 598)
    assert front.getpixel((0, 0))[3] == 0
    again = render_fixed_uv_slot2(design, original)
    assert again.output_png == result.output_png
    assert again.front_layer_png == result.front_layer_png
    assert again.atlas_sha256 == result.atlas_sha256


def test_no_random_angle_reprojection_or_recovery_for_hidden_letters():
    original = stock_sample()
    first = render_fixed_uv_slot2(design_sample(), original)
    changed = render_fixed_uv_slot2(design_sample(flip=True), original)
    assert first.atlas_sha256 != changed.atlas_sha256
    assert first.output_png != changed.output_png
    assert first.target_box == changed.target_box
    assert first.uv_box == changed.uv_box


def test_fail_closed_on_nontransparent_design_unapproved_camera_or_profile_drift():
    opaque = Image.new("RGB", (250, 120), (255, 245, 220))
    b = BytesIO()
    opaque.save(b, "PNG")
    with pytest.raises(FixedUvProjectionError, match="transparent alpha"):
        render_fixed_uv_slot2(b.getvalue(), stock_sample())
    with pytest.raises(FixedUvProjectionError, match="aspect"):
        render_fixed_uv_slot2(design_sample(), stock_sample((700, 1200)))
    with pytest.raises(FixedUvProjectionError, match="profile changed"):
        render_fixed_uv_slot2(design_sample(), stock_sample(), expected_profile_sha256="wrong")
