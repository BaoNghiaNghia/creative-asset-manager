"""BD-00 contract acceptance tests; no production integrations are invoked."""
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.branding_dna.contracts import (
    BestSellerObservation, BrandPickEvent, GeneratedCandidate, GroupCount,
    LearningRuleProposal, OutputLabelEvent, ProductMaster, ShotPlan,
    ShotRequest, SourceEvidence, SourceManifest, SMS_GROUPS, SCOUT_GROUPS, DEFAULT_COUNTS,
    route_shot,
)

NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)
SHA = "a" * 64


def base(**kwargs):
    return {"tenant_id": "tenant-A", "shop_id": "shop-1", **kwargs}


def source(kind, group=None, **kwargs):
    args = base(source_id=f"{kind}-1", product_master_id="master-1",
                sku="SKU-1", variant_sku="SKU-1-BLUE",
                source_revision="revision-1", kind=kind, rights="permitted")
    if kind == "sms":
        args.update(design_sha256=SHA, sms_angle="macro" if group == "detail" else "front")
    else:
        args.update(scout_review="approved", human_present=True)
    args.update(kwargs)
    return SourceEvidence(**args)


def shot(group, source_id=None, **kwargs):
    args = base(shot_id="shot-1", product_master_id="master-1",
                sku="SKU-1", variant_sku="SKU-1-BLUE", group=group,
                source_ids=(source_id,) if source_id else ())
    args.update(kwargs)
    return ShotRequest(**args)


def test_shot_distribution_fixture_and_version():
    fixture = Path(__file__).parent / "fixtures" / "balanced_10_shots.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    plan = ShotPlan.model_validate(payload)
    assert plan.total == 10
    assert {part.group: part.count for part in plan.groups} == DEFAULT_COUNTS
    assert plan.contract_version == "1.0"
    assert sum(part.group in SMS_GROUPS for part in plan.groups) == 4
    assert sum(part.group in SCOUT_GROUPS for part in plan.groups) == 2


def test_shot_plan_rejects_invalid_group_counts_and_unknown_schema():
    args = base(product_master_id="master-1", product_revision=1, plan_revision=1,
                dna_version_id="dna-1", playbook_version_id="playbook-1",
                groups=(GroupCount(group="studio", count=2),), total=1)
    with pytest.raises(ValidationError, match="Shot counts"):
        ShotPlan(**args)
    with pytest.raises(ValidationError, match="Duplicate"):
        ShotPlan(**{**args, "groups": (
            GroupCount(group="studio", count=1), GroupCount(group="studio", count=0))})
    with pytest.raises(ValidationError):
        ShotPlan(**{**args, "total": 2, "secret": "must not leak"})
    with pytest.raises(ValidationError):
        ShotPlan(**{**args, "total": 2, "contract_version": "2.0"})


@pytest.mark.parametrize("group", sorted(SMS_GROUPS))
def test_sms_only_branch_and_never_scout(group):
    sms = source("sms", group=group)
    requested = shot(group, sms.source_id, verified_claims=True, verified_variants=True)
    result = route_shot(requested, [sms])
    assert result.state == "ready"
    assert result.branch == "sms"
    assert result.usable_source_ids == (sms.source_id,)
    foreign = source("scout")
    with pytest.raises(ValueError, match="sms sources only"):
        route_shot(shot(group, foreign.source_id), [foreign])


@pytest.mark.parametrize("group", sorted(SCOUT_GROUPS))
def test_scout_only_branch_and_never_sms(group):
    scout = source("scout")
    ready = route_shot(shot(group, scout.source_id), [scout])
    assert ready.state == "ready"
    assert ready.branch == "scout"
    with pytest.raises(ValueError, match="scout sources only"):
        sms = source("sms")
        route_shot(shot(group, sms.source_id), [sms])


def test_no_fallback_missing_inputs_or_unreviewed_references():
    assert route_shot(shot("studio"), []).state == "needs_input"
    assert route_shot(shot("lifestyle"), []).state == "needs_reference"
    assert route_shot(shot("information", "sms-1"), [source("sms")]).state == "needs_input"
    assert route_shot(shot("variations", "sms-1"), [source("sms")]).state == "needs_input"
    assert route_shot(shot("detail", "sms-1"), [source("sms", sms_angle="front")]).state == "needs_input"
    for attrs in ({"rights": "denied"}, {"scout_review": "unreviewed"},
                  {"scout_review": "rejected"}, {"ai_tag": True}):
        ref = source("scout", **attrs)
        assert route_shot(shot("lifestyle", ref.source_id), [ref]).state == "needs_reference"
    assert route_shot(shot("ugc_human", "scout-1"),
                      [source("scout", human_present=False)]).state == "needs_reference"


def test_tenant_and_product_boundaries():
    for attrs in ({"tenant_id": "other"}, {"shop_id": "other"},
                  {"product_master_id": "different"}, {"sku": "different"},
                  {"variant_sku": "other"}):
        ref = source("sms", **attrs)
        with pytest.raises(ValueError, match="scope/product"):
            route_shot(shot("studio", ref.source_id), [ref])
    with pytest.raises(ValueError, match="Unknown source"):
        route_shot(shot("studio", "sms-1"), [])
    with pytest.raises(ValueError, match="Duplicate source"):
        route_shot(shot("studio", "sms-1"), [source("sms"), source("sms")])


def test_product_master_requires_real_asset_references_and_unique_variants():
    args = base(product_master_id="master-1", revision=1, sku="SKU-1",
                category="hats", artwork_asset_ids=("artwork-1",),
                base_product_ref_ids=("mockup-1",), placement={"surface": "front"},
                variants=({"sku": "SKU-1-BLUE", "color": "Blue"},))
    master = ProductMaster(**args)
    assert master.variants[0].sku == "SKU-1-BLUE"
    with pytest.raises(ValidationError):
        ProductMaster(**{**args, "variants": ({"sku": "repeat"}, {"sku": "repeat"})})
    with pytest.raises(ValidationError):
        ProductMaster(**{**args, "artwork_asset_ids": ()})


def test_product_and_image_picks_are_independent_and_unpick_is_not_negative():
    pick = BrandPickEvent(**base(target_type="image", target_id="img-1",
        action="pick", reason="good_photography", actor_id="curator-1",
        source_revision="gallery-1", occurred_at=NOW))
    assert pick.target_type == "image"
    assert pick.action != "negative"
    assert BrandPickEvent(**{**pick.model_dump(), "action": "unpick", "reason": None}).action == "unpick"
    with pytest.raises(ValidationError):
        BrandPickEvent(**{**pick.model_dump(), "reason": None})
    with pytest.raises(ValidationError):
        BrandPickEvent(**{**pick.model_dump(), "action": "unpick"})


def test_sales_provenance_and_unknown_must_not_be_exaggerated():
    args = base(product_id="product-1", source_quality="marketplace_proxy",
                metric_kind="rank", metric_value=12, platform="amazon", category="hats",
                region="US", observed_at=NOW, source_id="listing-1",
                verification_state="unverified")
    obs = BestSellerObservation(**args)
    assert obs.metric_kind == "rank"
    for update in ({"metric_kind": "orders"}, {"metric_kind": "revenue"},
                   {"verification_state": "verified"}, {"source_id": None}):
        with pytest.raises(ValidationError):
            BestSellerObservation(**{**args, **update})
    with pytest.raises(ValidationError):
        BestSellerObservation(**{**args, "source_quality": "unknown",
                                 "metric_value": 12, "verification_state": "unknown"})
    assert BestSellerObservation(**{**args, "source_quality": "unknown",
                                   "metric_value": None, "source_id": None,
                                   "verification_state": "unknown"}).metric_value is None
    assert BestSellerObservation(**{**args, "source_quality": "verified_sales",
                                   "metric_kind": "orders",
                                   "verification_state": "verified"}).metric_value == 12


def test_output_labels_never_infer_negative_from_absence():
    positive = OutputLabelEvent(**base(candidate_id="candidate-1", shot_id="shot-1",
                                      label="positive", actor_id="reviewer-1",
                                      occurred_at=NOW))
    assert positive.label == "positive"
    with pytest.raises(ValidationError):
        OutputLabelEvent(**{**positive.model_dump(), "label": "unreviewed"})
    with pytest.raises(ValidationError, match="requires an explicit reason"):
        OutputLabelEvent(**{**positive.model_dump(), "label": "negative"})
    negative = OutputLabelEvent(**{**positive.model_dump(), "label": "negative",
                                   "reject_reasons": ("wrong_color",)})
    assert negative.reject_reasons == ("wrong_color",)
    with pytest.raises(ValidationError):
        OutputLabelEvent(**{**negative.model_dump(), "label": "positive"})


def test_candidate_preserves_auditable_lineage():
    candidate = GeneratedCandidate(**base(candidate_id="candidate-1", shot_id="shot-1",
        product_master_id="master-1", product_revision=2, shot_plan_revision=1,
        dna_version_id="dna-approved-3", playbook_version_id="playbook-2",
        group="studio", source_branch="sms", source_ids=("sms-1",), output_asset_id="image-1",
        prompt_hash="prompt-sha", model_version="model-1", skill_version="skill-2"))
    assert candidate.output_asset_id == "image-1"
    assert candidate.product_revision == 2
    with pytest.raises(ValidationError, match="Candidate source branch"):
        GeneratedCandidate(**{**candidate.model_dump(), "source_branch": "scout"})


def test_source_manifest_rejects_cross_tenant_and_duplicate_evidence():
    sms = source("sms")
    manifest = SourceManifest(**base(product_master_id="master-1",
                                    manifest_revision=1, sources=(sms,)))
    assert manifest.sources[0].source_id == "sms-1"
    with pytest.raises(ValidationError, match="cross-product source manifest|Cross-tenant"):
        SourceManifest(**base(product_master_id="master-1",
                             manifest_revision=1,
                             sources=(source("sms", tenant_id="other"),)))
    with pytest.raises(ValidationError, match="Duplicate source"):
        SourceManifest(**base(product_master_id="master-1",
                             manifest_revision=1, sources=(sms, sms)))


def test_learning_proposal_requires_manual_approval_evaluation_and_branch():
    draft = base(proposal_id="proposal-1", group="studio", source_branch="sms")
    assert LearningRuleProposal(**draft).state == "draft"
    with pytest.raises(ValidationError, match="human approval"):
        LearningRuleProposal(**{**draft, "state": "canary"})
    with pytest.raises(ValidationError, match="immutable"):
        LearningRuleProposal(**{**draft, "source_branch": "scout"})
    assert LearningRuleProposal(**{**draft, "state": "canary", "approved_by": "curator-1",
                                 "offline_evaluation_id": "eval-1"}).state == "canary"
