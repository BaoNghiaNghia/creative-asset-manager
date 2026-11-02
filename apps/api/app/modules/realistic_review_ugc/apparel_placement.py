"""TikTokShop apparel design-zone visibility: source-to-candidate contract.

One source visual-analysis call locates the existing garment artwork. The
existing candidate-analysis call reports front/sleeve/collar visibility. This
module is deterministic: no second AI request, image editing or invented zones.

Coordinates in zone names are from the WEARER'S perspective. Mirrored selfies
must be interpreted anatomically rather than by screen-left/screen-right.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ApparelZone = Literal[
    "collar", "center_chest", "left_chest", "right_chest",
    "left_sleeve", "right_sleeve", "left_upper_arm", "right_upper_arm",
]
GarmentView = Literal["front", "three_quarter", "side", "back", "unknown"]

APPAREL_ZONES = frozenset((
    "collar", "center_chest", "left_chest", "right_chest",
    "left_sleeve", "right_sleeve", "left_upper_arm", "right_upper_arm",
))
APPAREL_PLACEMENT_VERSION = "apparel-placement-v1"


class SourceApparelPlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zone: ApparelZone
    priority: Literal["primary", "secondary"] = "secondary"
    confidence: float = Field(default=0.0, ge=0, le=1)


class CandidateApparelZone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zone: ApparelZone
    visible_ratio: float = Field(ge=0, le=1)
    occlusion_ratio: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)


@dataclass(frozen=True)
class ApparelSuitability:
    status: str | None  # rejected_context | needs_review | None (pass)
    reason: str | None
    score: float
    visibility_by_zone: dict[str, dict[str, float]]
    placement_version: str = APPAREL_PLACEMENT_VERSION


def source_placements(visual: dict[str, Any] | None) -> list[SourceApparelPlacement]:
    if not isinstance(visual, dict):
        return []
    valid: list[SourceApparelPlacement] = []
    used: set[str] = set()
    for entry in visual.get("apparel_placements") or []:
        try:
            zone = (entry if isinstance(entry, SourceApparelPlacement)
                    else SourceApparelPlacement.model_validate(entry))
        except (ValueError, TypeError):
            continue
        if zone.confidence < 0.55 or zone.zone in used:
            continue
        used.add(zone.zone)
        valid.append(zone)
        if len(valid) >= 8:
            break
    # A visual analyzer can return multiple primary zones. Preserve the most
    # confident single primary but keep every visible secondary placement.
    primary = next((row for row in valid if row.priority == "primary"), None)
    if primary is None and valid:
        primary = max(valid, key=lambda row: row.confidence)
    return [
        SourceApparelPlacement(
            zone=row.zone,
            priority="primary" if row.zone == getattr(primary, "zone", None) else "secondary",
            confidence=row.confidence,
        )
        for row in valid
    ]


def apparel_placement_queries(
    garment: str, placements: list[SourceApparelPlacement],
) -> list[str]:
    """Pinterest composition hints, not a new visual-scoring AI call."""
    apparel = garment if garment in {"t-shirt", "shirt", "sweatshirt", "sweater", "hoodie", "polo"} else "shirt"
    zones = {row.zone for row in placements}
    queries: list[str] = []
    if zones & {"center_chest", "left_chest", "right_chest"}:
        queries.append(f"person wearing {apparel} front upper body chest unobstructed candid photo")
    if "collar" in zones:
        queries.append(f"{apparel} worn upper torso neckline visible hair away candid photo")
    if zones & {"left_sleeve", "right_sleeve", "left_upper_arm", "right_upper_arm"}:
        queries.append(f"person wearing {apparel} front three quarter arm sleeves visible standing")
    if not queries:
        queries.append(f"person wearing {apparel} front facing torso visible candid photo")
    return queries[:3]


def evaluate_apparel_suitability(
    *,
    placements: list[SourceApparelPlacement],
    garment_view: str,
    garment_frame_ratio: float | None,
    observations: list[CandidateApparelZone],
) -> ApparelSuitability:
    """Fail visibly incompatible photos closed, defer uncertain evidence.

    A secondary sleeve/collar that cannot be confirmed never qualifies for
    automatic approval; it can be manually reviewed or used in another angle.
    """
    observed = {
        item.zone: {
            "visible_ratio": round(item.visible_ratio, 3),
            "occlusion_ratio": round(item.occlusion_ratio, 3),
            "confidence": round(item.confidence, 3),
        }
        for item in observations
    }
    if not placements:
        return ApparelSuitability("needs_review", "APPAREL_PLACEMENT_UNKNOWN", 0.0, observed)
    if garment_view == "back":
        return ApparelSuitability("rejected_context", "APPAREL_WRONG_VIEW_BACK", 0.0, observed)
    if garment_frame_ratio is not None and garment_frame_ratio < 0.16:
        return ApparelSuitability("rejected_context", "APPAREL_GARMENT_TOO_SMALL", 0.0, observed)
    if garment_view == "unknown" or garment_frame_ratio is None:
        return ApparelSuitability("needs_review", "APPAREL_VIEW_UNCERTAIN", 0.0, observed)

    total, weighted, uncertainty = 0.0, 0.0, None
    for placement in placements:
        zone = observed.get(placement.zone)
        is_primary = placement.priority == "primary"
        weight = 0.68 if is_primary else 0.32 / max(1, len(placements) - 1)
        total += weight
        if zone is None or zone["confidence"] < 0.68:
            uncertainty = uncertainty or ("APPAREL_PRIMARY_UNCERTAIN" if is_primary
                                          else "APPAREL_SECONDARY_UNCERTAIN")
            continue
        visible = zone["visible_ratio"]
        occluded = zone["occlusion_ratio"]
        weighted += weight * visible * (1 - occluded)
        if visible < (0.55 if is_primary else 0.48):
            reason = "APPAREL_PRIMARY_NOT_VISIBLE" if is_primary else "APPAREL_SECONDARY_NOT_VISIBLE"
            if is_primary:
                return ApparelSuitability("rejected_context", reason, 0.0, observed)
            uncertainty = uncertainty or reason
        if occluded > (0.30 if is_primary else 0.34):
            reason = "APPAREL_PRIMARY_OCCLUDED" if is_primary else "APPAREL_SECONDARY_OCCLUDED"
            if is_primary:
                return ApparelSuitability("rejected_context", reason, 0.0, observed)
            uncertainty = uncertainty or reason
    score = round(min(1.0, max(0.0, weighted / max(total, 1e-6))), 4)
    if uncertainty:
        return ApparelSuitability("needs_review", uncertainty, score, observed)
    return ApparelSuitability(None, None, score, observed)
