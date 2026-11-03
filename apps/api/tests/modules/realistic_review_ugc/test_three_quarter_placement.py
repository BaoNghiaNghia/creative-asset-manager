"""Independent 3/4 calibration: revisions, tenant isolation and geometry validation."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcThreeQuarterPlacementSettingModel
from app.modules.realistic_review_ugc.three_quarter_placement import (
    DEFAULT_THREE_QUARTER_CORNERS, ThreeQuarterPlacementConflict,
    ThreeQuarterPlacementInvalid, read_three_quarter_placement,
    save_three_quarter_placement, validate_three_quarter_corners,
)


def test_three_quarter_tenant_color_revision_and_read_does_not_write():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcThreeQuarterPlacementSettingModel.__table__.create(engine)
    with Session(engine) as session:
        baseline = read_three_quarter_placement(session, "tenant-a", "white-black")
        assert baseline["revision"] == 0
        assert baseline["corners"] == [list(x) for x in DEFAULT_THREE_QUARTER_CORNERS]
        assert session.query(RrugcThreeQuarterPlacementSettingModel).count() == 0
        saved = save_three_quarter_placement(session, "tenant-a", "person-a", "white-black",
                                             baseline["corners"], 0)
        assert saved["revision"] == 1
        assert read_three_quarter_placement(session, "tenant-a", "natural-brown")["revision"] == 0
        assert read_three_quarter_placement(session, "tenant-b", "white-black")["revision"] == 0
        with pytest.raises(ThreeQuarterPlacementConflict):
            save_three_quarter_placement(session, "tenant-a", "person-b", "white-black",
                                         baseline["corners"], 0)
        assert read_three_quarter_placement(session, "tenant-a", "white-black")["revision"] == 1
    engine.dispose()


@pytest.mark.parametrize("bad", [
    [],
    [[0.2, 0.2]],
    [[0.2, 0.2], [0.5, 0.2], [0.2, 0.5], [0.5, 0.5]],
    [[0.2, 0.2], [0.2, 0.2], [0.5, 0.5], [0.2, 0.5]],
    [[-0.2, 0.2], [0.5, 0.2], [0.5, 0.5], [0.2, 0.5]],
    [[float("nan"), 0.2], [0.5, 0.2], [0.5, 0.5], [0.2, 0.5]],
    # Crossed, collapsed and out-of-image placements remain invalid.
    [[0.20, 0.20], [0.80, 0.80], [0.20, 0.80], [0.80, 0.20]],
    [[0.20, 0.20], [0.21, 0.20], [0.21, 0.21], [0.20, 0.21]],
    [[0.10, 0.10], [1.10, 0.10], [0.90, 0.90], [0.10, 0.90]],
])
def test_invalid_three_quarter_geometry_rejected(bad):
    with pytest.raises(ThreeQuarterPlacementInvalid):
        validate_three_quarter_corners(bad)


def test_safe_default_and_previous_factory_field_remain_usable():
    assert validate_three_quarter_corners(DEFAULT_THREE_QUARTER_CORNERS) == [
        list(point) for point in DEFAULT_THREE_QUARTER_CORNERS
    ]
    # An older *valid* placement should not require destructive migration.
    previous = [
        [0.316, 0.351], [0.647, 0.356], [0.657, 0.552], [0.294, 0.529],
    ]
    assert validate_three_quarter_corners(previous) == previous


def test_free_drag_outside_old_crown_bounds_is_persisted_exactly():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcThreeQuarterPlacementSettingModel.__table__.create(engine)
    wide = [[0.18, 0.12], [0.75, 0.24], [0.69, 0.69], [0.11, 0.56]]
    with Session(engine) as session:
        assert validate_three_quarter_corners(wide) == wide
        saved = save_three_quarter_placement(
            session, "tenant-a", "user-a", "white-black", wide, 0,
        )
        assert saved["revision"] == 1
        assert read_three_quarter_placement(session, "tenant-a", "white-black")["corners"] == wide
        with pytest.raises(ThreeQuarterPlacementInvalid, match="non-crossing"):
            save_three_quarter_placement(
                session, "tenant-a", "user-a", "white-black",
                [[0.20, 0.20], [0.80, 0.80], [0.20, 0.80], [0.80, 0.20]], 1,
            )
        assert read_three_quarter_placement(session, "tenant-a", "white-black")["revision"] == 1
    engine.dispose()
