"""Tenant-scoped 8869 three-quarter artwork projection anchors.

These are preview calibration values only. Completed outputs and queued jobs
are not modified; the Stage 1 FRONT field remains the size authority.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcThreeQuarterPlacementSettingModel

DEFAULT_THREE_QUARTER_CORNERS = (
    (0.336, 0.320), (0.638, 0.335), (0.652, 0.548), (0.296, 0.534),
)

# Same verified factory photo coordinates as ThreeQuarterProjection.ts.
# Each corner is confined to its own inner front-panel window, excluding
# the side embroidery/visor. Both field dimensions must remain usable.
THREE_QUARTER_CORNER_LIMITS = (
    ((0.310, 0.435), (0.305, 0.405)),  # top left
    ((0.555, 0.662), (0.305, 0.405)),  # top right
    ((0.570, 0.676), (0.458, 0.568)),  # bottom right
    ((0.274, 0.400), (0.458, 0.555)),  # bottom left
)


class ThreeQuarterPlacementInvalid(ValueError):
    pass


class ThreeQuarterPlacementConflict(RuntimeError):
    pass


def validate_color_key(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) or len(value) > 64:
        raise ThreeQuarterPlacementInvalid("Invalid 8869 stock color key.")
    return value


def validate_three_quarter_corners(raw: object) -> list[list[float]]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ThreeQuarterPlacementInvalid("Exactly four corner coordinates are required.")
    result = []
    for point in raw:
        if (not isinstance(point, (list, tuple)) or len(point) != 2 or
                any(isinstance(v, bool) or not isinstance(v, (float, int)) or
                    not math.isfinite(float(v)) or not 0 <= float(v) <= 1 for v in point)):
            raise ThreeQuarterPlacementInvalid("Each point must contain two finite 0–1 coordinates.")
        result.append([round(float(v), 6) for v in point])
    tl, tr, br, bl = result
    # Ordered clockwise TL, TR, BR, BL. Reject crossed/flat warps and extreme distortions.
    cross = lambda a, b, c: (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
    turns = [cross(result[i], result[(i+1)%4], result[(i+2)%4]) for i in range(4)]
    if (any(turn <= 0.002 for turn in turns) or tr[0] - tl[0] < 0.22 or
            br[0] - bl[0] < 0.23 or bl[1] - tl[1] < 0.12 or
            br[1] - tr[1] < 0.12):
        raise ThreeQuarterPlacementInvalid("The four points must form a readable, non-crossing front embroidery field.")
    for index, point in enumerate(result):
        x_limit, y_limit = THREE_QUARTER_CORNER_LIMITS[index]
        if not (x_limit[0] <= point[0] <= x_limit[1] and
                y_limit[0] <= point[1] <= y_limit[1]):
            raise ThreeQuarterPlacementInvalid(
                f"Point {index + 1} must stay inside the safe front crown bounds."
            )
    return result


def read_three_quarter_placement(session: Session, tenant_id: str, color_key: str) -> dict:
    validate_color_key(color_key)
    row = session.get(RrugcThreeQuarterPlacementSettingModel, (tenant_id, color_key))
    if row is None:
        return {"color_key": color_key, "corners": [list(p) for p in DEFAULT_THREE_QUARTER_CORNERS],
                "revision": 0, "updated_at": None}
    return {"color_key": color_key, "corners": row.corners_json, "revision": row.revision,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def save_three_quarter_placement(session: Session, tenant_id: str, user_id: str,
                                  color_key: str, corners: object, expected_revision: int) -> dict:
    validate_color_key(color_key)
    corners = validate_three_quarter_corners(corners)
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise ThreeQuarterPlacementInvalid("Expected revision must be a non-negative integer.")
    row = session.get(RrugcThreeQuarterPlacementSettingModel, (tenant_id, color_key), with_for_update=True)
    actual = row.revision if row else 0
    if actual != expected_revision:
        raise ThreeQuarterPlacementConflict("Three-quarter placement changed in another tab. Reload before saving.")
    now = datetime.now(timezone.utc)
    if row is None:
        row = RrugcThreeQuarterPlacementSettingModel(tenant_id=tenant_id, color_key=color_key)
        session.add(row)
    row.corners_json = corners
    row.revision = actual + 1
    row.updated_by_user_id = user_id
    row.updated_at = now
    session.commit()
    return {"color_key": color_key, "corners": corners, "revision": row.revision,
            "updated_at": now.isoformat()}
