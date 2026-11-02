"""Slot 1 provides two tenant-scoped output URLs with immutable reference links."""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.ecommerce_pipeline import EcommerceService
from app.modules.realistic_review_ugc.model import (
    RrugcSourcePlanModel, RrugcEcommerceJobModel, RrugcImageOutputVersionModel,
)
from app.modules.realistic_review_ugc.router import (
    ecommerce_output_versions, router,
)


def test_slot1_two_urls_visible_during_pass_b_and_stored_versions():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        RrugcSourcePlanModel.__table__,
        RrugcEcommerceJobModel.__table__,
        RrugcImageOutputVersionModel.__table__,
        ProcessingJobModel.__table__,
    ])
    with Session(engine) as db:
        db.add(RrugcSourcePlanModel(
            id="plan", tenant_id="tenant", root_folder_id="hat",
            source_file_id="design", source_name="design.png",
            source_relative_path="design.png", source_revision="rev",
            source_mime_type="image/png", created_by_user_id="u",
        ))
        db.add(RrugcEcommerceJobModel(
            id="front", tenant_id="tenant", source_plan_id="plan",
            source_revision="rev", stage="prod_s2_ec1",
            stock_sha256="f" * 64, skill_source="internal",
            skill_name="cam-slot1-thread-reference-inpaint-v2",
            skill_version="2.2.0", skill_bundle_sha256="f" * 64,
            front_preview_remote_file_id="material-a",
            front_preview_content_type="image/png",
            front_preview_size_bytes=5000, front_preview_design_sha256="a"*64,
            status="running", created_by_user_id="u",
        ))
        db.add(RrugcImageOutputVersionModel(
            id="version-a", tenant_id="tenant", job_id="front",
            stage="ecommerce", version=1,
            remote_file_id="original-front", content_type="image/png",
            font_info_json={"slot1_embroidery_pipeline": {
                "thread_material_asset": "material-old",
                "version": "thread-reference-inpaint-v2",
            }},
        ))
        db.commit()
        row = EcommerceService(db).list(tenant_id="tenant", source_ids=["plan"])[0]
        assert row["embroidery_reference_url"].endswith(
            "/front/output?kind=embroidery_reference",
        )
        assert row["output_url"] is None
        versions = ecommerce_output_versions(
            "front", session=db, principal=SimpleNamespace(active_tenant_id="tenant"),
        )
        assert versions[0]["embroidery_reference_url"].endswith(
            "/front/output?kind=embroidery_reference&version=1",
        )
        assert versions[0]["output_url"].endswith("/front/output?version=1")
        db.get(RrugcEcommerceJobModel, "front").status = "completed"
        db.get(RrugcEcommerceJobModel, "front").output_remote_file_id = "front-b"
        db.flush()
        row = EcommerceService(db).list(tenant_id="tenant", source_ids=["plan"])[0]
        assert row["output_url"].endswith("/front/output")
        assert row["embroidery_reference_url"].endswith("kind=embroidery_reference")
        assert EcommerceService(db).list(tenant_id="other", source_ids=["plan"]) == []
    routes = {(item.path, method) for item in router.routes
              for method in getattr(item, "methods", ())}
    assert ("/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/{job_id}/retry-inpaint",
            "POST") in routes
    engine.dispose()
