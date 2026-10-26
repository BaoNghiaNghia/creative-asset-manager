"""Saved front guide calibration: validation, persistence, tenant scope, immutability."""
from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.ecommerce_front_reference import render_front_reference
from app.modules.realistic_review_ugc.front_placement import (
    DEFAULT_FRONT_PLACEMENT, FrontPlacementConflict, FrontPlacementInvalid,
    front_placement_prompt, job_front_placement, pin_front_placement,
    read_front_placement, save_front_placement, validate_front_placement,
    validate_front_design_size,
)
from app.modules.realistic_review_ugc.model import RrugcFrontPlacementSettingModel, RrugcImageOutputVersionModel


def _sample(**changes):
    return {**DEFAULT_FRONT_PLACEMENT, **changes}


@pytest.fixture
def engine():
    db = create_engine("sqlite+pysqlite:///:memory:")
    RrugcFrontPlacementSettingModel.__table__.create(db)
    RrugcImageOutputVersionModel.__table__.create(db)
    yield db
    db.dispose()


@pytest.mark.parametrize(("method", "endpoint"), [
    ("GET", "get_products_front_placement"),
    ("PUT", "put_products_front_placement"),
])
def test_front_placement_routes_are_not_shadowed_by_product_dynamic_routes(method, endpoint):
    from starlette.routing import Match
    from app.modules.realistic_review_ugc.router import router

    path = "/api/v1/realistic-review-ugc/blueprint/front-placement"
    resolved = [r for r in router.routes if r.matches({
        "type": "http", "path": path, "method": method,
    })[0] == Match.FULL]
    assert resolved, f"{method} {path} was not found"
    assert resolved[0].endpoint.__name__ == endpoint


def test_default_matches_old_blueprint_and_is_not_saved_by_read(engine):
    with Session(engine) as session:
        assert read_front_placement(session, "tenant-a") == {
            **DEFAULT_FRONT_PLACEMENT, "revision": 0, "updated_at": None,
        }
        assert session.query(RrugcFrontPlacementSettingModel).count() == 0


def test_geometry_is_persisted_across_sessions_and_is_tenant_isolated(engine):
    first = _sample(left=0.12, top=0.27, width=0.75, height=0.30,
                    upper_line=0.14, lower_line=0.71)
    with Session(engine) as session:
        saved = save_front_placement(session, "tenant-a", "user-a", first, 0)
        assert saved["revision"] == 1
        assert read_front_placement(session, "tenant-b")["revision"] == 0
    with Session(engine) as session:
        loaded = read_front_placement(session, "tenant-a")
        assert all(loaded[key] == value for key, value in first.items())
        assert loaded["revision"] == 1
        assert pin_front_placement(session, "tenant-a") == first
        assert "left 12.00%" in front_placement_prompt(first)
        changed = save_front_placement(session, "tenant-a", "user-a",
                                       _sample(left=0.2, upper_line=0.15), 1)
        assert changed["revision"] == 2
        assert read_front_placement(session, "tenant-b")["left"] == DEFAULT_FRONT_PLACEMENT["left"]


def test_optimistic_lock_prevents_overwriting_other_tabs(engine):
    with Session(engine) as session:
        save_front_placement(session, "tenant-a", "user-a", _sample(), 0)
    with Session(engine) as session:
        with pytest.raises(FrontPlacementConflict, match="another tab"):
            save_front_placement(session, "tenant-a", "user-b",
                                 _sample(left=0.22), 0)
        assert read_front_placement(session, "tenant-a")["revision"] == 1


@pytest.mark.parametrize("change", [
    {"left": -0.1}, {"left": 0.8, "width": 0.7},
    {"top": 0.9, "height": 0.2}, {"width": 0.09},
    {"height": 0.05}, {"upper_line": 0.8, "lower_line": 0.5},
    {"upper_line": 0.57, "lower_line": 0.60}, {"height": float("nan")},
    {"upper_line": 1.2}, {"width": float("inf")},
    {"top": True},
])
def test_invalid_geometry_fails_closed(change):
    with pytest.raises(FrontPlacementInvalid):
        validate_front_placement(_sample(**change))


def test_queued_job_snapshot_not_changed_by_subsequent_workspace_calibration():
    snapshot = dict(DEFAULT_FRONT_PLACEMENT)
    new_setting = _sample(left=0.22, top=0.19, width=0.66, height=0.3)
    assert job_front_placement(snapshot) == snapshot
    assert job_front_placement(None) == snapshot
    assert job_front_placement(snapshot) != new_setting


def test_front_reference_uses_custom_guide_for_new_jobs_without_editing_stock():
    hat = Image.new("RGB", (500, 625), (210, 200, 180))
    artwork = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    ImageDraw.Draw(artwork).rectangle((60, 15, 240, 85), fill=(24, 35, 220))
    def encoded(image):
        blob = BytesIO()
        image.save(blob, "PNG")
        return blob.getvalue()
    calibrated = render_front_reference(encoded(hat), encoded(artwork),
                                        guide=(0.12, 0.18, 0.75, 0.31))
    old = render_front_reference(encoded(hat), encoded(artwork))
    assert calibrated != old
    with Image.open(BytesIO(calibrated)) as result:
        assert result.getpixel((0, 0)) == (210, 200, 180)
        assert result.getpixel((250, 100)) == (210, 200, 180)
        assert result.getpixel((250, 220)) == (24, 35, 220)


def test_stage1_size_endpoint_updates_only_persisted_version_setting(engine):
    from types import SimpleNamespace
    from app.modules.realistic_review_ugc.router import (
        FrontDesignSizeRequest, put_stage1_front_design_size,
    )
    with Session(engine) as session:
        session.add(RrugcImageOutputVersionModel(
            id="00000000-0000-4000-8000-000000000003",
            tenant_id="tenant-a", stage="stage1",
            job_id="job-a", version=3, remote_file_id="persisted-design",
            front_design_size=100,
        ))
        session.commit()
        result = put_stage1_front_design_size(
            "job-a", 3, FrontDesignSizeRequest(front_design_size=85),
            session=session, principal=SimpleNamespace(active_tenant_id="tenant-a"),
        )
        assert result == {"front_design_size": 85}
        row = session.get(RrugcImageOutputVersionModel, "00000000-0000-4000-8000-000000000003")
        assert row.front_design_size == 85
        assert row.remote_file_id == "persisted-design"
        assert row.version == 3


def test_design_specific_size_is_pinned_per_stage1_version_and_tenant(engine):
    with Session(engine) as session:
        session.add_all([
            RrugcImageOutputVersionModel(
                id="00000000-0000-4000-8000-000000000001", tenant_id="tenant-a", stage="stage1",
                job_id="job-a", version=1, remote_file_id="remote-a",
                front_design_size=65,
            ),
            RrugcImageOutputVersionModel(
                id="00000000-0000-4000-8000-000000000002", tenant_id="tenant-b", stage="stage1",
                job_id="job-b", version=1, remote_file_id="remote-b",
                front_design_size=180,
            ),
        ])
        session.commit()
        a = pin_front_placement(session, "tenant-a", "00000000-0000-4000-8000-000000000001")
        assert a["design_size"] == 65
        assert "65% of the contain-fit" in front_placement_prompt(a)
        assert job_front_placement(a) == a
        assert pin_front_placement(session, "tenant-a", "00000000-0000-4000-8000-000000000002")["design_size"] == 100
        assert pin_front_placement(session, "tenant-b", "00000000-0000-4000-8000-000000000002")["design_size"] == 180
        assert "design_size" not in pin_front_placement(session, "tenant-a")


@pytest.mark.parametrize("value", [39, 201, 66, 100.0, True, None, "85"])
def test_invalid_zoom_values_rejected(value):
    with pytest.raises(FrontPlacementInvalid):
        validate_front_design_size(value)


def test_reference_zoom_preserves_aspect_and_stays_inside_field():
    from PIL import ImageChops

    hat = Image.new("RGB", (500, 625), "white")
    design = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    ImageDraw.Draw(design).rectangle((10, 10, 190, 90), fill="blue")
    def encoded(image):
        out = BytesIO()
        image.save(out, "PNG")
        return out.getvalue()
    bounds = []
    for percent in (40, 100, 200):
        rendered = render_front_reference(encoded(hat), encoded(design), design_size=percent)
        with Image.open(BytesIO(rendered)) as final:
            painted = ImageChops.difference(final.convert("RGB"), hat).getbbox()
        assert painted is not None
        x0, y0, x1, y1 = painted
        bounds.append((x1 - x0, y1 - y0))
        assert 500 * .152 <= x0 < x1 <= 500 * (.152 + .696)
        assert 625 * .309 <= y0 < y1 <= 625 * (.309 + .231)
    assert bounds[0][0] < bounds[1][0] <= bounds[2][0]
    assert bounds[0][1] < bounds[1][1] <= bounds[2][1]
