"""G3 human approval and bounded, structural Style DNA overrides.

The AI-produced DNA fields remain immutable. Review decisions live only in
manual_override_json and are auditable independently. The accepted effective
structure cannot contain source lettering, custom phrases, URLs or glyph paths:
all human-editable text fields are fixed enum choices or generated node IDs.
No AI provider, Scout or Stage 1 worker is invoked here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.realistic_review_ugc.typography_dna_contract import (
    TypographyDnaContractError, validate_typography_dna,
)

REVIEW_SCHEMA = "typography-review/v1"
REVIEW_DECISIONS = frozenset({"accepted", "needs_review", "rejected"})
ACCEPT_REASONS = frozenset({
    "approved", "region_corrected", "font_corrected", "layout_corrected",
    "overlap_corrected", "low_confidence_reviewed",
})
REVIEW_REASONS = frozenset({
    "region_uncertain", "font_mismatch", "layout_mismatch",
    "overlap_unreadable", "low_confidence", "near_duplicate",
    "rights_concern", "copy_risk", "no_typography", "other",
})
VERIFIED_RIGHTS = frozenset({"owned", "licensed", "permitted_reference"})


class TypographyReviewError(ValueError):
    """Reject invalid edits without storing untrusted source words."""


class RegionOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)
    w: float = Field(gt=0, le=1, allow_inf_nan=False)
    h: float = Field(gt=0, le=1, allow_inf_nan=False)


class FontMoodOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: Literal["serif", "sans_serif", "slab_serif", "script", "handwritten", "display", "monospace"]
    secondary: Literal["serif", "sans_serif", "slab_serif", "script", "handwritten", "display", "monospace"] | None = None
    weight: Literal["light", "regular", "medium", "bold", "heavy"]
    contrast: Literal["low", "medium", "high"]
    proportions: Literal["condensed", "normal", "wide"]


class LayoutNodeOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^n(?:[1-9]|1[0-2])$")
    role: Literal["primary", "secondary"]
    size_ratio: float = Field(ge=0.01, le=10, allow_inf_nan=False)
    path_type: Literal["straight", "arch", "curve"]
    align: Literal["left", "center", "right"]


class LayoutEdgeOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str = Field(alias="from", pattern=r"^n(?:[1-9]|1[0-2])$")
    to: str = Field(pattern=r"^n(?:[1-9]|1[0-2])$")
    relation: Literal["above", "below", "overlap", "inside"]
    strength: float = Field(ge=0, le=1, allow_inf_nan=False)


class LayoutGraphOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: list[LayoutNodeOverride] = Field(min_length=1, max_length=12)
    edges: list[LayoutEdgeOverride] = Field(max_length=36)


class OverlapRuleOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: str = Field(pattern=r"^n(?:[1-9]|1[0-2])$")
    b: str = Field(pattern=r"^n(?:[1-9]|1[0-2])$")
    region: Literal["top", "bottom", "left", "right", "center"]
    reading_priority: Literal["primary_first", "secondary_first", "balanced"]
    # Strong overlap destroys legibility when transferred to a new keyword.
    max_occlusion_ratio: float = Field(ge=0, le=0.35, allow_inf_nan=False)


class StructuralOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    typography_region: RegionOverride | None = None
    font_mood: FontMoodOverride | None = None
    layout_graph: LayoutGraphOverride | None = None
    overlap_rules: list[OverlapRuleOverride] | None = Field(default=None, max_length=24)

    @model_validator(mode="after")
    def nonempty(self):
        if all(getattr(self, key) is None for key in (
            "typography_region", "font_mood", "layout_graph", "overlap_rules",
        )):
            raise ValueError("At least one structural field must be overridden")
        return self


class TypographyDnaReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accepted", "needs_review", "rejected"]
    reason_code: Literal[
        "approved", "region_corrected", "font_corrected", "layout_corrected",
        "overlap_corrected", "low_confidence_reviewed",
        "region_uncertain", "font_mismatch", "layout_mismatch",
        "overlap_unreadable", "low_confidence", "near_duplicate",
        "rights_concern", "copy_risk", "no_typography", "other",
    ]
    override: StructuralOverride | None = None

    @model_validator(mode="after")
    def reason_and_edit_consistent(self):
        if self.decision == "accepted":
            if self.reason_code not in ACCEPT_REASONS:
                raise ValueError("Acceptance requires an approved review reason")
            if self.override is not None and self.reason_code == "approved":
                raise ValueError("Edited DNA needs an explicit correction reason")
        else:
            if self.reason_code not in REVIEW_REASONS:
                raise ValueError("Review or rejection requires a reason")
            if self.override is not None:
                raise ValueError("Only accepted DNA may apply a structural override")
        return self


def _base_dna_payload(dna) -> dict:
    return {
        "schema_version": dna.schema_version,
        "analyzer_version": dna.analyzer_version,
        "image_hash": dna.image_hash,
        "typography_region": dna.typography_region_json,
        "style_family": dna.style_family,
        "font_mood": dna.font_mood_json,
        "layout_graph": dna.layout_graph_json,
        "overlap_rules": dna.overlap_rules_json,
        "confidence": dna.confidence,
    }


def effective_reviewed_dna(reference, dna) -> dict | None:
    """Only returns DNA that is explicitly accepted, still licensed and current.

    A raw result or accepted result whose source hash, rights, reviewer schema
    or editorial approval has changed must NEVER be passed to the future
    Style Lab/Stage 1 reference matcher.
    """
    review = dna.manual_override_json
    if not isinstance(review, dict) or review.get("schema_version") != REVIEW_SCHEMA:
        return None
    if review.get("decision") != "accepted":
        return None
    if (review.get("image_hash") != dna.image_hash
            or review.get("analyzer_version") != dna.analyzer_version):
        return None
    if (dna.confidence < 0.65 and review.get("reason_code") != "low_confidence_reviewed"):
        return None
    if (reference.tenant_id != dna.tenant_id or reference.id != dna.reference_id
            or reference.content_hash != dna.image_hash
            or reference.source_rights_status not in VERIFIED_RIGHTS
            or reference.editorial_status != "approved"):
        return None
    changes = review.get("override")
    if changes is not None and (not isinstance(changes, dict) or not set(changes).issubset({
        "typography_region", "font_mood", "layout_graph", "overlap_rules",
    })):
        return None
    payload = _base_dna_payload(dna)
    if changes:
        payload.update(changes)
    try:
        return validate_typography_dna(payload, source_sha256=reference.content_hash)
    except TypographyDnaContractError:
        return None


def apply_human_review(dna, request: TypographyDnaReviewRequest) -> dict:
    """Validate the prospective effective DNA before it can be accepted."""
    changes = request.override.model_dump(exclude_none=True, by_alias=True) if request.override else None
    if request.decision == "accepted":
        if dna.confidence < 0.65 and request.reason_code != "low_confidence_reviewed":
            raise TypographyReviewError("Low-confidence DNA requires explicit human confirmation")
        trial = _base_dna_payload(dna)
        if changes:
            trial.update(changes)
        try:
            validate_typography_dna(trial, source_sha256=dna.image_hash)
        except TypographyDnaContractError as exc:
            raise TypographyReviewError("Invalid reviewed typography structure") from exc
    return {
        "schema_version": REVIEW_SCHEMA,
        "decision": request.decision,
        "reason_code": request.reason_code,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "analyzer_version": dna.analyzer_version,
        "image_hash": dna.image_hash,
        "override": changes,
    }


def review_view(reference, dna) -> dict:
    review = dna.manual_override_json if isinstance(dna.manual_override_json, dict) else {}
    accepted = effective_reviewed_dna(reference, dna)
    invalidated = review.get("decision") == "accepted" and accepted is None
    return {
        "decision": "needs_review" if invalidated else review.get("decision", "pending"),
        "reason_code": "approval_invalidated" if invalidated else review.get("reason_code"),
        "reviewed_at": review.get("reviewed_at"),
        "has_override": bool(review.get("override")),
        "effective_dna": accepted,
    }
