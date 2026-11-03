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
    # Old oversize guides from the screenshot must never be persisted again.
    [[0.18, 0.12], [0.75, 0.24], [0.69, 0.69], [0.11, 0.56]],
    # The former loose validation allowed technically convex but wrong crown shapes.
    [[0.336, 0.415], [0.638, 0.335], [0.652, 0.548], [0.296, 0.534]],
    [[0.336, 0.320], [0.680, 0.335], [0.652, 0.548], [0.296, 0.534]],
    [[0.434, 0.400], [0.555, 0.401], [0.570, 0.458], [0.399, 0.459]],
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


def test_outside_crown_save_is_rejected_without_a_db_write():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcThreeQuarterPlacementSettingModel.__table__.create(engine)
    with Session(engine) as session:
        with pytest.raises(ThreeQuarterPlacementInvalid, match="Point 1"):
            save_three_quarter_placement(
                session, "tenant-a", "user-a", "white-black",
                [[0.18, 0.12], [0.638, 0.335], [0.652, 0.548], [0.296, 0.534]], 0,
            )
        assert session.query(RrugcThreeQuarterPlacementSettingModel).count() == 0
    engine.dispose()
