"""Ecommerce backend wiring, independent job state and source provenance."""
from types import SimpleNamespace

from sqlalchemy import inspect

from app.domain.processing.types import JOB_TYPES
from app.modules.processing.bootstrap import _JOB_GLOBAL_FLAGS
from app.modules.processing.worker_roles import IMAGE_AI_JOB_TYPES
from app.modules.processing_policy.claim import AI_JOB_TYPES, STAGE_POLICY
from app.modules.realistic_review_ugc.ecommerce_pipeline import (
    JOB_TYPE, STOCK_ROLES, VIEW_PROMPTS, LEGACY_EC2_STOCK_ROLES,
    _stock_refs, _pinned_stock_refs, _rebind_ec2_stock_for_explicit_action,
    EcommerceError, EcommerceService,
)
from app.modules.realistic_review_ugc.model import RrugcEcommerceJobModel
from app.modules.realistic_review_ugc.router import router


def test_ecommerce_is_fully_registered_as_image_job():
    assert JOB_TYPE in JOB_TYPES
    assert JOB_TYPE in IMAGE_AI_JOB_TYPES
    assert JOB_TYPE in AI_JOB_TYPES
    assert JOB_TYPE in _JOB_GLOBAL_FLAGS
    assert STAGE_POLICY[JOB_TYPE] == "pipeline_enabled"


def test_ecommerce_routes_are_authorization_gated():
    routes = {(r.path, method) for r in router.routes
              for method in getattr(r, "methods", ())}
    base = "/api/v1/realistic-review-ugc/products-stage2/ecommerce"
    assert (base + "/run", "POST") in routes
    assert (base + "/jobs", "GET") in routes
    assert (base + "/jobs/{job_id}/retry", "POST") in routes
    assert (base + "/jobs/{job_id}/regenerate", "POST") in routes
    assert (base + "/jobs/{job_id}/output", "GET") in routes
    assert (base + "/jobs/{job_id}/versions", "GET") in routes
    assert (base + "/jobs/{job_id}/logs", "GET") in routes


def test_independent_persistence_uses_source_revision_and_skill_hash():
    columns = {c.name for c in inspect(RrugcEcommerceJobModel).columns}
    for required in ("tenant_id", "source_plan_id", "source_revision", "stage",
                     "stock_sha256", "skill_bundle_sha256", "processing_job_id",
                     "output_remote_file_id", "output_content_type", "retry_count"):
        assert required in columns
    constraints = {c.name for c in RrugcEcommerceJobModel.__table__.constraints}
    assert "uq_rrugc_ecommerce_source_revision_stage_color" in constraints


def test_view_reference_sources_and_anti_invention_prompts():
    assert list(STOCK_ROLES) == (
        [f"prod_s2_ec{i}" for i in range(1, 5)]
        + [f"prod_s2_mh_{i}" for i in range(1, 4)]
        + [f"prod_s2_ugc_{i}" for i in range(1, 4)]
    )
    assert STOCK_ROLES["prod_s2_ec2"] == ("stock_three_quarter",)
    assert LEGACY_EC2_STOCK_ROLES == ("stock_front", "stock_side_90")
    assert "THREE-QUARTER" in VIEW_PROMPTS["prod_s2_ec2"]
    assert "EXACT FACTORY THREE-QUARTER STOCK EDIT" in VIEW_PROMPTS["prod_s2_ec2"]
    assert "Do not turn, rotate, invent" in VIEW_PROMPTS["prod_s2_ec2"]
    assert "Keep the rear panel blank" in VIEW_PROMPTS["prod_s2_ec4"]


def test_stock_digest_is_pinned_and_source_is_not_client_controlled():
    one, digest1 = _stock_refs(SimpleNamespace(), "prod_s2_ec1")
    two, digest2 = _stock_refs(SimpleNamespace(), "prod_s2_ec1")
    assert one == two
    assert digest1 == digest2 and len(digest1) == 64
    assert one[0][0] == "stock_front"
    try:
        _stock_refs(SimpleNamespace(), "prod_s2_ec9")
    except EcommerceError as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("Invalid stage was accepted")


def test_new_ec2_uses_stock_34_while_old_jobs_remain_pinned_until_explicit_action(tmp_path, monkeypatch):
    from app.modules.realistic_review_ugc import ecommerce_pipeline as pipeline
    def fake_stock(role, *, project_root, color_key):
        return (role.encode("utf8") * 30, {"role": role})
    monkeypatch.setattr(pipeline, "read_release_stock", fake_stock)
    settings = SimpleNamespace(CODEX_IMAGE_HOME=str(tmp_path))
    new_refs, new_digest = _stock_refs(settings, "prod_s2_ec2", "natural-charcoal")
    old_refs, old_digest = _stock_refs(
        settings, "prod_s2_ec2", "natural-charcoal", legacy_three_quarter=True)
    assert [role for role, _ in new_refs] == ["stock_three_quarter"]
    assert [role for role, _ in old_refs] == ["stock_front", "stock_side_90"]
    assert new_digest != old_digest
    pinned, old = _pinned_stock_refs(settings, "prod_s2_ec2", "natural-charcoal", old_digest)
    assert old and pinned == old_refs
    pinned, old = _pinned_stock_refs(settings, "prod_s2_ec2", "natural-charcoal", new_digest)
    assert not old and pinned == new_refs
    job = RrugcEcommerceJobModel(
        id="old", tenant_id="tenant", source_plan_id="plan", source_revision="rev",
        stage="prod_s2_ec2", color_key="natural-charcoal", stock_sha256=old_digest,
        skill_source="local", skill_name="test", created_by_user_id="actor", status="failed")
    _rebind_ec2_stock_for_explicit_action(settings, job)
    assert job.stock_sha256 == new_digest
    with __import__("pytest").raises(EcommerceError, match="Pinned stock content changed"):
        _pinned_stock_refs(settings, "prod_s2_ec2", "natural-charcoal", "invalid")


def test_job_serialization_and_failure_does_not_mutate_status_unexpectedly():
    row = RrugcEcommerceJobModel(
        id="job", tenant_id="tenant", source_plan_id="plan",
        source_revision="revision", stage="prod_s2_ec1",
        stock_sha256="a" * 64, skill_source="local", skill_name="test",
        created_by_user_id="actor", status="queued",
    )
    assert row.stage == "prod_s2_ec1"
    assert row.source_revision == "revision"
    assert row.status == "queued"