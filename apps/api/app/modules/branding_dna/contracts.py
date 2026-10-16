"""Versioned, side-effect-free contracts for Branding DNA + Product Creative BD-00.

No endpoints, scans, queue dispatch, DB writes, model generation or label learning
are enabled by importing this module. Server-side tenant/rights checks are still
required when these contracts are integrated into APIs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ContractVersion = Literal["1.0"]
ShotGroup = Literal["studio", "detail", "information", "variations", "lifestyle", "ugc_human"]
SourceBranch = Literal["sms", "scout"]
PickTarget = Literal["product", "image"]
BestSellerQuality = Literal["verified_sales", "marketplace_proxy", "team_reported", "unknown"]
ReviewLabel = Literal["positive", "negative"]

SMS_GROUPS = frozenset(("studio", "detail", "information", "variations"))
SCOUT_GROUPS = frozenset(("lifestyle", "ugc_human"))
DEFAULT_COUNTS: dict[str, int] = {
    "studio": 2, "detail": 2, "information": 1,
    "variations": 1, "lifestyle": 2, "ugc_human": 2,
}


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    contract_version: ContractVersion = "1.0"


class ScopedContract(StrictContract):
    tenant_id: str = Field(min_length=1, max_length=255)
    shop_id: str = Field(min_length=1, max_length=64)


class BrandPickEvent(ScopedContract):
    target_type: PickTarget
    target_id: str = Field(min_length=1)
    action: Literal["pick", "unpick"]
    reason: Literal[
        "good_design", "good_photography", "brand_representative",
        "good_layout", "good_lifestyle", "other"
    ] | None = None
    actor_id: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_pick(self):
        if self.action == "pick" and not self.reason:
            raise ValueError("Brand Pick requires an explicit reason")
        if self.action == "unpick" and self.reason is not None:
            raise ValueError("Unpick is not a negative label and must not have a reason")
        return self


class BestSellerObservation(ScopedContract):
    product_id: str = Field(min_length=1)
    variant_sku: str | None = None
    source_quality: BestSellerQuality
    metric_kind: Literal["orders", "revenue", "rank", "engagement", "reported"]
    metric_value: float | None = Field(default=None, ge=0)
    unit: str | None = None
    platform: Literal["amazon", "etsy", "shopify"]
    category: str = Field(min_length=1)
    region: str = Field(min_length=2)
    period_start: datetime | None = None
    period_end: datetime | None = None
    observed_at: datetime
    source_id: str | None = None
    verification_state: Literal["verified", "unverified", "unknown"]

    @model_validator(mode="after")
    def check_sales_claim(self):
        if self.source_quality == "unknown":
            if self.metric_value is not None or self.verification_state != "unknown":
                raise ValueError("Unknown Best Seller must not assert a metric")
        elif not self.source_id:
            raise ValueError("Best Seller observation requires source provenance")
        if self.source_quality == "marketplace_proxy" and self.metric_kind in ("orders", "revenue"):
            raise ValueError("Marketplace proxy cannot claim order/revenue measurements")
        if self.source_quality == "verified_sales":
            if (self.metric_kind not in ("orders", "revenue")
                    or self.verification_state != "verified"
                    or self.metric_value is None):
                raise ValueError("Verified sales require a verified sales metric")
        elif self.verification_state == "verified":
            raise ValueError("Only verified sales may be marked verified")
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("Invalid observation period")
        return self


class ProductVariant(StrictContract):
    sku: str = Field(min_length=1)
    color: str | None = None


class ProductMaster(ScopedContract):
    product_master_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    sku: str = Field(min_length=1)
    category: str = Field(min_length=1)
    artwork_asset_ids: tuple[str, ...] = Field(min_length=1)
    base_product_ref_ids: tuple[str, ...] = Field(min_length=1)
    placement: dict[str, str] = Field(min_length=1)
    protected_features: tuple[str, ...] = ()
    variants: tuple[ProductVariant, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_variants(self):
        skus = [item.sku for item in self.variants]
        if len(skus) != len(set(skus)):
            raise ValueError("Duplicate product variant SKU")
        if not all(x.strip() for x in self.artwork_asset_ids + self.base_product_ref_ids):
            raise ValueError("Product reference ID cannot be blank")
        return self


class SourceEvidence(ScopedContract):
    source_id: str = Field(min_length=1)
    product_master_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    variant_sku: str | None = None
    source_revision: str = Field(min_length=1)
    kind: SourceBranch
    rights: Literal["permitted", "unknown", "denied"]
    design_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    sms_angle: Literal["front", "back", "side", "45", "macro"] | None = None
    scout_review: Literal["approved", "rejected", "unreviewed", "ai"] | None = None
    ai_tag: bool = False
    human_present: bool | None = None
    source_uri_hash: str | None = None

    @model_validator(mode="after")
    def cross_branch_evidence(self):
        if self.kind == "sms" and self.scout_review is not None:
            raise ValueError("SMS evidence cannot carry Scout review")
        if self.kind == "scout" and (self.sms_angle is not None or self.design_sha256 is not None):
            raise ValueError("Scout evidence cannot masquerade as SMS mockup")
        return self


class SourceManifest(ScopedContract):
    """Immutable revision of a product source bundle; SMS and Scout remain separate."""
    product_master_id: str = Field(min_length=1)
    manifest_revision: int = Field(ge=1)
    sources: tuple[SourceEvidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def verify_scope(self):
        ids = [s.source_id for s in self.sources]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate source IDs in manifest")
        for row in self.sources:
            if (row.tenant_id != self.tenant_id or row.shop_id != self.shop_id
                    or row.product_master_id != self.product_master_id):
                raise ValueError("Cross-tenant or cross-product source manifest")
        return self


class GroupCount(StrictContract):
    group: ShotGroup
    count: int = Field(ge=0, le=100)


class ShotPlan(ScopedContract):
    product_master_id: str = Field(min_length=1)
    product_revision: int = Field(ge=1)
    plan_revision: int = Field(ge=1)
    dna_version_id: str = Field(min_length=1)
    playbook_version_id: str = Field(min_length=1)
    groups: tuple[GroupCount, ...] = Field(min_length=1)
    total: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def validate_counts(self):
        names = [g.group for g in self.groups]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate output group")
        if sum(g.count for g in self.groups) != self.total:
            raise ValueError("Shot counts must match total")
        return self


class ShotRequest(ScopedContract):
    shot_id: str = Field(min_length=1)
    product_master_id: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    variant_sku: str | None = None
    group: ShotGroup
    source_ids: tuple[str, ...] = ()
    verified_claims: bool = False
    verified_variants: bool = False


class ShotRoute(StrictContract):
    group: ShotGroup
    branch: SourceBranch
    state: Literal["ready", "needs_input", "needs_reference"]
    usable_source_ids: tuple[str, ...] = ()


def route_shot(shot: ShotRequest, evidence: list[SourceEvidence]) -> ShotRoute:
    """Fail-closed A/B router. This function never calls SMS or Scout services."""
    branch: SourceBranch = "sms" if shot.group in SMS_GROUPS else "scout"
    selected: list[SourceEvidence] = []
    by_id = {row.source_id: row for row in evidence}
    if len(by_id) != len(evidence):
        raise ValueError("Duplicate source evidence ID")
    for source_id in shot.source_ids:
        source = by_id.get(source_id)
        if source is None:
            raise ValueError("Unknown source evidence ID")
        if (source.tenant_id != shot.tenant_id or source.shop_id != shot.shop_id
                or source.product_master_id != shot.product_master_id or source.sku != shot.sku
                or (shot.variant_sku is not None and source.variant_sku != shot.variant_sku)):
            raise ValueError("Source evidence does not match shot scope/product")
        if source.kind != branch:
            raise ValueError(f"{shot.group} must use {branch} sources only")
        selected.append(source)

    if branch == "sms":
        usable = [s.source_id for s in selected if s.rights == "permitted"
                  and s.design_sha256 is not None and len(s.design_sha256) == 64
                  and s.sms_angle is not None
                  and (shot.group != "detail" or s.sms_angle == "macro")]
        if shot.group == "information" and not shot.verified_claims:
            usable = []
        if shot.group == "variations" and not shot.verified_variants:
            usable = []
        state = "ready" if usable else "needs_input"
    else:
        usable = [s.source_id for s in selected if s.rights == "permitted"
                  and s.scout_review == "approved" and not s.ai_tag
                  and (shot.group != "ugc_human" or s.human_present is True)]
        state = "ready" if usable else "needs_reference"

    return ShotRoute(group=shot.group, branch=branch, state=state,
                     usable_source_ids=tuple(usable))


class GeneratedCandidate(ScopedContract):
    candidate_id: str = Field(min_length=1)
    shot_id: str = Field(min_length=1)
    product_master_id: str = Field(min_length=1)
    product_revision: int = Field(ge=1)
    shot_plan_revision: int = Field(ge=1)
    dna_version_id: str = Field(min_length=1)
    playbook_version_id: str = Field(min_length=1)
    group: ShotGroup
    source_branch: SourceBranch
    source_ids: tuple[str, ...] = Field(min_length=1)
    output_asset_id: str = Field(min_length=1)
    prompt_hash: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def enforce_candidate_branch(self):
        expected = "sms" if self.group in SMS_GROUPS else "scout"
        if self.source_branch != expected:
            raise ValueError("Candidate source branch conflicts with shot group")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("Candidate must not repeat source IDs")
        return self


class OutputLabelEvent(ScopedContract):
    candidate_id: str = Field(min_length=1)
    shot_id: str = Field(min_length=1)
    label: ReviewLabel
    reject_reasons: tuple[Literal[
        "wrong_product_form", "artwork_changed", "wrong_text", "wrong_color",
        "wrong_placement", "fake_embroidery", "unnatural_hands", "scene_mismatch",
        "poor_composition", "low_resolution", "brand_mismatch",
        "policy_or_claim_error", "other"
    ], ...] = ()
    actor_id: str = Field(min_length=1)
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_label(self):
        if self.label == "negative" and not self.reject_reasons:
            raise ValueError("Negative output label requires an explicit reason")
        if self.label == "positive" and self.reject_reasons:
            raise ValueError("Positive output label cannot have rejection reasons")
        return self


class LearningRuleProposal(ScopedContract):
    proposal_id: str = Field(min_length=1)
    group: ShotGroup
    source_branch: SourceBranch
    offline_evaluation_id: str | None = None
    state: Literal["draft", "approved", "canary", "rolled_back"] = "draft"
    approved_by: str | None = None

    @model_validator(mode="after")
    def manual_approval_gate(self):
        expected = "sms" if self.group in SMS_GROUPS else "scout"
        if self.source_branch != expected:
            raise ValueError("Learning cannot change immutable A/B routing")
        if self.state != "draft" and (not self.approved_by or not self.offline_evaluation_id):
            raise ValueError("Rule promotion requires human approval and offline evaluation")
        return self
