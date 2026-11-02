"""Slot 1 Retry Inpaint vs Full Regenerate lifecycle without Codex charges."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc import ecommerce_pipeline as pipeline
from app.modules.realistic_review_ugc.model import RrugcSourcePlanModel, RrugcEcommerceJobModel, RrugcFrontPlacementSettingModel


def setup_rows():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        RrugcSourcePlanModel.__table__, RrugcEcommerceJobModel.__table__,
        RrugcFrontPlacementSettingModel.__table__,
    ])
    session = Session(engine)
    session.add(RrugcSourcePlanModel(
        id="source", tenant_id="tenant", root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
        source_file_id="artwork", source_relative_path="artwork.png",
        source_name="artwork.png", source_mime_type="image/png",
        source_revision="revision", created_by_user_id="operator",
    ))
    session.add(RrugcEcommerceJobModel(
        id="job", tenant_id="tenant", source_plan_id="source",
        source_revision="revision", stage="prod_s2_ec1",
        color_key="natural-black", stock_sha256="a" * 64,
        skill_source="internal", skill_name=pipeline.SLOT1_DIRECT_NAME,
        skill_version=pipeline.SLOT1_DIRECT_PREVIOUS_VERSION,
        skill_bundle_sha256=pipeline.SLOT1_DIRECT_PREVIOUS_SHA256,
        created_by_user_id="operator", status="completed",
        output_remote_file_id="old-completed-front",
        front_preview_remote_file_id="saved-real-macro",
        front_preview_content_type="image/png",
        front_preview_size_bytes=5000,
        front_preview_design_sha256="b" * 64,
    ))
    session.commit()
    return engine, session


def test_inpaint_retry_reuses_macro_and_preserves_last_completed_front(monkeypatch):
    engine, session = setup_rows()
    captured = []
    monkeypatch.setattr(pipeline.EcommerceService, "_enqueue",
                        lambda self, row, **kw: captured.append(kw))
    service = pipeline.EcommerceService(session)
    result = service.retry_inpaint(tenant_id="tenant", job_id="job")
    assert result == {
        "job_id": "job", "status": "queued", "embroidery_reference_reused": True,
    }
    row = session.get(RrugcEcommerceJobModel, "job")
    assert row.status == "queued"
    assert row.skill_version == pipeline.SLOT1_DIRECT_VERSION
    assert row.skill_bundle_sha256 == pipeline.SLOT1_DIRECT_SHA256
    assert row.front_preview_remote_file_id == "saved-real-macro"
    assert row.output_remote_file_id == "old-completed-front"
    assert not captured[-1].get("regenerate_material_reference")
    try:
        service.retry_inpaint(tenant_id="tenant", job_id="job")
    except pipeline.EcommerceError as exc:
        assert exc.code == "ecommerce_inpaint_retry_unavailable"
    else:
        raise AssertionError("Running jobs must not be retried twice")
    session.close()
    engine.dispose()


def test_full_regenerate_pins_force_new_pass_a_without_deleting_previous_macro(monkeypatch):
    engine, session = setup_rows()
    captured = []
    monkeypatch.setattr(pipeline.EcommerceService, "_enqueue",
                        lambda self, row, **kw: captured.append(kw))
    monkeypatch.setattr(pipeline, "pin_front_placement", lambda *a: None)
    monkeypatch.setattr(pipeline, "_rebind_ec2_stock_for_explicit_action",
                        lambda *_: None)
    result = pipeline.EcommerceService(session).regenerate(
        tenant_id="tenant", job_id="job",
    )
    assert result["status"] == "queued"
    assert captured and captured[-1]["regenerate_material_reference"] is True
    row = session.get(RrugcEcommerceJobModel, "job")
    assert row.front_preview_remote_file_id == "saved-real-macro"
    assert row.output_remote_file_id == "old-completed-front"
    assert row.skill_version == pipeline.SLOT1_DIRECT_VERSION
    session.close()
    engine.dispose()
