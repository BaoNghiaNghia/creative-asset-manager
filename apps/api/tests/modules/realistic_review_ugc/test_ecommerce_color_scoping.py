"""Regression tests for independent 8869 hat color jobs without external AI."""
from sqlalchemy import select
from app.modules.realistic_review_ugc.ecommerce_pipeline import EcommerceService, EcommerceError
from app.modules.realistic_review_ugc.model import RrugcEcommerceJobModel
import pytest
from test_ecommerce_queue import ecommerce_db


def test_new_color_does_not_reuse_an_existing_completed_front(ecommerce_db, monkeypatch):
    import app.modules.realistic_review_ugc.ecommerce_pipeline as pipeline
    session, settings = ecommerce_db
    monkeypatch.setattr(pipeline, "_stock_refs",
                        lambda settings, stage, color_key="natural-black": ([], "f" * 64))
    service = EcommerceService(session, settings)
    old = service.queue(tenant_id="tenant-001", user_id="operator",
                        source_plan_id="plan-001", stage="prod_s2_ec1")
    original = session.get(RrugcEcommerceJobModel, old["job_id"])
    original.status = "completed"
    original.output_remote_file_id = "kept-original"
    session.commit()
    new = service.queue(tenant_id="tenant-001", user_id="operator",
                        source_plan_id="plan-001", stage="prod_s2_ec1", color_key="natural-red")
    assert new["job_id"] != old["job_id"]
    assert original.output_remote_file_id == "kept-original"
    assert {row.color_key for row in session.scalars(select(RrugcEcommerceJobModel))} == {
        "natural-black", "natural-red"
    }
    with pytest.raises(EcommerceError, match="Skill 1"):
        service.queue(tenant_id="tenant-001", user_id="operator",
                      source_plan_id="plan-001", stage="prod_s2_ec3", color_key="natural-red")
    new_front = session.get(RrugcEcommerceJobModel, new["job_id"])
    new_front.status = "completed"
    new_front.output_remote_file_id = "red-front-only"
    session.commit()
    side = service.queue(tenant_id="tenant-001", user_id="operator",
                         source_plan_id="plan-001", stage="prod_s2_ec3", color_key="natural-red")
    pinned = session.get(RrugcEcommerceJobModel, side["job_id"])
    assert pinned.front_reference_remote_file_id == "red-front-only"
    assert pinned.color_key == "natural-red"
