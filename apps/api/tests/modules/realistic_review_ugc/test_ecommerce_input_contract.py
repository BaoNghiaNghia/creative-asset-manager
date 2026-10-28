from app.modules.realistic_review_ugc.ecommerce_input_contract import (
    SHOTS, build_execution_preflight, missing_persisted_inputs,
)
import pytest

def test_four_skill_roles_do_not_share_front_side_back_references():
    assert [s.stage for s in SHOTS] == [f"prod_s2_ec{i}" for i in range(1, 5)]
    assert [len(s.required_roles) for s in SHOTS] == [2, 3, 3, 1]
    assert SHOTS[1].required_roles == ("design", "embroidered_front", "stock_three_quarter")
    assert SHOTS[-1].embroidery_surface is None

def test_temporary_preview_urls_cannot_be_used_as_durable_references():
    missing = missing_persisted_inputs("prod_s2_ec2", {
        "design": "data:image/png;base64,abc",
        "embroidered_front": "blob:session-1",
        "stock_front": "/rrugc/blueprint/fronts/natural-black.webp",
        "stock_three_quarter": "/api/v1/realistic-review-ugc/products/hat-stock/natural-black/three-quarter",
    })
    assert missing == ("design", "embroidered_front", "stock_three_quarter")

def test_three_quarter_stock_required_even_with_completed_front():
    assets = {"design": "drive:design-01", "embroidered_front": "drive:completed-front"}
    missing = missing_persisted_inputs("prod_s2_ec2", assets)
    assert missing == ("stock_three_quarter",)
    assert build_execution_preflight(
        "prod_s2_ec2", assets, worker_ready=True, skill_ready=True
    )["ready"] is False
    assets["stock_three_quarter"] = "sha256:" + "a" * 64
    assert build_execution_preflight(
        "prod_s2_ec2", assets, worker_ready=True, skill_ready=True
    )["ready"] is True


def test_worker_and_skill_gates_remain_closed_even_when_inputs_are_saved():
    assets = {"design": "drive:design-01", "stock_front": "drive:cap-01"}
    assert build_execution_preflight("prod_s2_ec1", assets, worker_ready=False, skill_ready=True)["status"] == "worker_pending"
    assert build_execution_preflight("prod_s2_ec1", assets, worker_ready=True, skill_ready=False)["status"] == "missing_skill"
    assert build_execution_preflight("prod_s2_ec1", assets, worker_ready=True, skill_ready=True)["ready"] is True

def test_unknown_stage_rejected():
    with pytest.raises(ValueError):
        missing_persisted_inputs("prod_s2_ec7", {})