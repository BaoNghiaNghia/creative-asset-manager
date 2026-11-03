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
])
def test_invalid_three_quarter_geometry_rejected(bad):
    with pytest.raises(ThreeQuarterPlacementInvalid):
        validate_three_quarter_corners(bad)
