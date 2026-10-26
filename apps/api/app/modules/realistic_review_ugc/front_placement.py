"""Durable, bounded front embroidery calibration shared by Blueprint and new Product jobs.

The coordinates are proportions of the unzoomed official 4:5 stock photograph.
Queued jobs pin a snapshot in their processing payload; older and completed jobs
continue to use the original default and are never migrated or re-queued.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcFrontPlacementSettingModel

DEFAULT_FRONT_PLACEMENT: dict[str, float] = {
    "left": 0.152,
    "top": 0.309,
    "width": 0.696,
    "height": 0.231,
    "upper_line": 0.19,
    "lower_line": 0.63,
}
GEOMETRY_KEYS = tuple(DEFAULT_FRONT_PLACEMENT)


class FrontPlacementInvalid(ValueError):
    pass


class FrontPlacementConflict(RuntimeError):
    pass


def validate_front_placement(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict) or set(raw) != set(GEOMETRY_KEYS):
        raise FrontPlacementInvalid("Exactly six front placement values are required.")
    if any(isinstance(raw[key], bool) or not isinstance(raw[key], (int, float))
           or not math.isfinite(float(raw[key])) for key in GEOMETRY_KEYS):
        raise FrontPlacementInvalid("Front placement values must be finite numbers.")
    v = {key: round(float(raw[key]), 6) for key in GEOMETRY_KEYS}
    if (not 0 <= v["left"] <= 0.9
            or not 0 <= v["top"] <= 0.94
            or not 0.10 <= v["width"] <= 0.95
            or not 0.06 <= v["height"] <= 0.65
            or v["left"] + v["width"] > 1.000001
            or v["top"] + v["height"] > 1.000001
            or not 0 <= v["upper_line"] < v["lower_line"] <= 1
            or v["lower_line"] - v["upper_line"] < 0.04):
        raise FrontPlacementInvalid("The embroidery area or blue height guides exceed the stock photograph.")
    return v


def read_front_placement(session: Session, tenant_id: str) -> dict:
    row = session.get(RrugcFrontPlacementSettingModel, tenant_id)
    if row is None:
        return {**DEFAULT_FRONT_PLACEMENT, "revision": 0, "updated_at": None}
    return {
        "left": row.guide_left, "top": row.guide_top,
        "width": row.guide_width, "height": row.guide_height,
        "upper_line": row.upper_line, "lower_line": row.lower_line,
        "revision": row.revision,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def pin_front_placement(session: Session, tenant_id: str) -> dict[str, float]:
    """Return immutable geometry values without revision metadata."""
    current = read_front_placement(session, tenant_id)
    return {key: current[key] for key in GEOMETRY_KEYS}


def job_front_placement(snapshot: object) -> dict[str, float]:
    """Old queued jobs deliberately keep the original calibration."""
    if snapshot is None:
        return dict(DEFAULT_FRONT_PLACEMENT)
    return validate_front_placement(snapshot)


def front_placement_prompt(snapshot: dict[str, float]) -> str:
    x, y, w, h = (snapshot[key] for key in ("left", "top", "width", "height"))
    return (
        "Calibrated embroidery field on the original complete stock photo: "
        f"left {x * 100:.2f}%, top {y * 100:.2f}%, "
        f"width {w * 100:.2f}%, height {h * 100:.2f}%. "
        f"Two saved maximum-height alignment lines are at "
        f"{snapshot['upper_line'] * 100:.2f}% and {snapshot['lower_line'] * 100:.2f}% "
        "of the same photograph height; do not draw guide lines in the final image. "
        "Place the front artwork entirely within the calibrated embroidery field, "
        "without distorting the original lettering. "
    )


def save_front_placement(session: Session, tenant_id: str, actor_id: str,
                         raw: object, expected_revision: int) -> dict:
    v = validate_front_placement(raw)
    if expected_revision < 0:
        raise FrontPlacementInvalid("The expected revision must be non-negative.")
    row = session.get(RrugcFrontPlacementSettingModel, tenant_id, with_for_update=True)
    actual = row.revision if row else 0
    if expected_revision != actual:
        raise FrontPlacementConflict("Placement changed in another tab. Reload the saved placement and try again.")
    now = datetime.now(timezone.utc)
    if row is None:
        row = RrugcFrontPlacementSettingModel(tenant_id=tenant_id)
        session.add(row)
    row.guide_left, row.guide_top = v["left"], v["top"]
    row.guide_width, row.guide_height = v["width"], v["height"]
    row.upper_line, row.lower_line = v["upper_line"], v["lower_line"]
    row.revision = actual + 1
    row.updated_by_user_id = actor_id
    row.updated_at = now
    session.commit()
    return {**v, "revision": row.revision, "updated_at": now.isoformat()}
