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
)
from app.modules.realistic_review_ugc.model import RrugcFrontPlacementSettingModel


def _sample(**changes):
    return {**DEFAULT_FRONT_PLACEMENT, **changes}


@pytest.fixture
def engine():
    db = create_engine("sqlite+pysqlite:///:memory:")
    RrugcFrontPlacementSettingModel.__table__.create(db)
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
