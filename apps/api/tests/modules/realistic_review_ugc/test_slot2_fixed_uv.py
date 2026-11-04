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

# This is the snapshot which FRONT stores for the calibration sample, not a
# hidden fallback inside Slot 2. Test changes to the snapshot explicitly.
SAVED_FRONT = {
    "left": .212142, "top": .328244, "width": .560727, "height": .232605,
    "upper_line": .270188, "lower_line": .597924, "design_size": 100,
}


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
    assert PIPELINE_VERSION == "fixed-shared-uv-v2"
    assert len(sha) == 64
    assert profile["views"]["front"]["width"] == profile["views"]["front"]["height"] == 1254
    assert profile["views"]["front"]["nodes"][2][2] == [617.601897, 557.5]
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
    atlas, box = _shared_texture(
        _input_design(data), profile["logicalArea"],
        profile["views"]["front"], SAVED_FRONT,
    )
    assert atlas.shape == (round(1024*150/268), 1024, 4)
    assert abs((box[0]+box[2])/2-.5) < 1e-12
    expected_front_y = (
        (SAVED_FRONT["top"] + SAVED_FRONT["height"]/2)*1254 - 300
    ) / 515
    assert abs((box[1]+box[3])/2 - expected_front_y) < 1e-8
    # Both views use the SAME 1px alpha-trim margin, so compare to the
    # actual alpha bbox rather than unpadded painted rectangle dimensions.
    assert abs(((box[2]-box[0])*935.203794)/((box[3]-box[1])*515)-343/118) < .02
    front = _project_atlas(atlas, profile["views"]["front"], (1254, 1254))
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
    result = render_fixed_uv_slot2(
        design, original, front_placement=SAVED_FRONT, expected_profile_sha256=sha,
    )
    assert hashlib.sha256(design).hexdigest() == result.design_sha256
    assert result.profile_sha256 == sha
    assert result.visible_pixel_count > 1000
    assert 0 < result.visible_fraction <= 1
    assert len(result.atlas_sha256) == 64
    assert result.front_anchor == pytest.approx((617.601897/1254, 557.5/1254))
    assert result.three_quarter_anchor == pytest.approx((455.948967/1254, 521.164319/1254))
    assert result.uv_box[0] + result.uv_box[2] == pytest.approx(1)
    assert (result.uv_box[1] + result.uv_box[3])/2 == pytest.approx(
        ((SAVED_FRONT["top"] + SAVED_FRONT["height"]/2)*1254 - 300)/515
    )
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
    assert front.size == (1254, 1254)
    assert front.getpixel((0, 0))[3] == 0
    again = render_fixed_uv_slot2(design, original, front_placement=SAVED_FRONT)
    assert again.output_png == result.output_png
    assert again.front_layer_png == result.front_layer_png
    assert again.atlas_sha256 == result.atlas_sha256


def test_no_random_angle_reprojection_or_recovery_for_hidden_letters():
    original = stock_sample()
    first = render_fixed_uv_slot2(design_sample(), original, front_placement=SAVED_FRONT)
    changed = render_fixed_uv_slot2(design_sample(flip=True), original, front_placement=SAVED_FRONT)
    assert first.atlas_sha256 != changed.atlas_sha256
    assert first.output_png != changed.output_png
    assert first.target_box == changed.target_box
    assert first.uv_box == changed.uv_box


def test_occlusion_changes_only_visibility_not_uv_position_or_scale():
    """Clipping a hidden half must not trigger a second fit/recenter."""
    profile, _ = load_profile()
    texture, box = _shared_texture(
        _input_design(design_sample()), profile["logicalArea"],
        profile["views"]["front"], SAVED_FRONT,
    )
    full_view = profile["views"]["threeQuarter"]
    clipped_view = {**full_view, "visibleMaskPath": "M450,250 H1000 V900 H450 Z"}
    original = _project_atlas(texture, full_view, (1254, 1254))
    clipped = _project_atlas(texture, clipped_view, (1254, 1254))
    visible_orig = original[..., 3] > 3
    visible_clip = clipped[..., 3] > 3
    assert 0 < int(np.count_nonzero(visible_clip)) < int(np.count_nonzero(visible_orig))
    assert not bool(np.any(visible_clip[:, :450]))
    assert np.allclose(
        original[visible_clip], clipped[visible_clip], rtol=0, atol=1e-6,
    )
    # The only change was the mask. Atlas/UV and the profile's nodes remain
    # untouched, even where an entire source glyph is now hidden.
    _, again_box = _shared_texture(
        _input_design(design_sample()), profile["logicalArea"],
        profile["views"]["front"], SAVED_FRONT,
    )
    assert again_box == box
    assert full_view["nodes"] == clipped_view["nodes"]


def test_pinned_v1_profile_remains_available_with_the_legacy_unmodified_placement():
    """An old queued job must not silently acquire the new saved-front UV."""
    from app.modules.realistic_review_ugc.slot2_fixed_uv import (
        LEGACY_PROFILE_FILE, LEGACY_PIPELINE_VERSION, _legacy_texture,
    )
    old_sha = hashlib.sha256(LEGACY_PROFILE_FILE.read_bytes()).hexdigest()
    legacy, selected_sha = load_profile(old_sha)
    assert LEGACY_PIPELINE_VERSION == "fixed-shared-uv-v1"
    assert selected_sha == old_sha
    assert legacy["schemaVersion"] == 1
    design = design_sample()
    texture, expected_uv = _legacy_texture(
        _input_design(design), legacy["logicalArea"],
    )
    projected = render_fixed_uv_slot2(
        design, stock_sample(), expected_profile_sha256=old_sha,
    )
    assert projected.profile_sha256 == old_sha
    assert projected.profile_id == "cream-navy-user-axis-v1"
    assert projected.uv_mapping_version == "legacy-centered-v1"
    assert projected.uv_box == tuple(round(n, 8) for n in expected_uv)
    assert projected.atlas_sha256 == hashlib.sha256(texture.tobytes()).hexdigest()
    assert Image.open(BytesIO(projected.front_layer_png)).size == (399, 598)


def test_fail_closed_on_nontransparent_design_unapproved_camera_or_profile_drift():
    opaque = Image.new("RGB", (250, 120), (255, 245, 220))
    b = BytesIO()
    opaque.save(b, "PNG")
    with pytest.raises(FixedUvProjectionError, match="transparent alpha"):
        render_fixed_uv_slot2(b.getvalue(), stock_sample(), front_placement=SAVED_FRONT)
    with pytest.raises(FixedUvProjectionError, match="aspect"):
        render_fixed_uv_slot2(design_sample(), stock_sample((700, 1200)), front_placement=SAVED_FRONT)
    with pytest.raises(FixedUvProjectionError, match="profile changed"):
        render_fixed_uv_slot2(design_sample(), stock_sample(), front_placement=SAVED_FRONT, expected_profile_sha256="wrong")