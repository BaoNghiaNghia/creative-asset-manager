"""Stage 5 Before must be a saved Pinterest lifestyle input, never the product hat."""
from app.modules.realistic_review_ugc.model import RrugcStage2JobModel
from app.modules.realistic_review_ugc.stage5_before_source import stage5_before_references


def job(references=None, hat=None):
    return RrugcStage2JobModel(
        tenant_id="tenant-a", source_plan_id="plan-a", campaign_id="campaign",
        source_revision="rev", skill_name="skill",
        selected_candidate_ids_json=["pin-1"],
        selected_reference_snapshot_json=references or [],
        selected_source_snapshot_json=hat,
        idempotency_key="test", created_by_user_id="operator",
    )


def test_before_is_pinterest_and_not_the_product_hat():
    item = job(
        references=[{
            "candidate_id": "pin-1", "position": 0,
            "remote_file_id": "pinterest-lifestyle-selfie",
            "content_type": "image/jpeg", "image_url": "https://pinterest.com/...",
        }],
        hat={"remote_file_id": "white-studio-hat", "content_type": "image/png"},
    )
    assert stage5_before_references(item) == [
        ("pinterest-lifestyle-selfie", "image/jpeg", None),
    ]


def test_multiple_references_respect_model_input_order_and_skip_duplicates():
    item = job(references=[
        {"position": 2, "remote_file_id": "pin-three", "content_type": "image/webp"},
        {"position": 0, "remote_file_id": "pin-one", "content_type": "image/jpeg"},
        {"position": 1, "remote_file_id": "pin-two", "content_type": "image/png"},
        {"position": 9, "remote_file_id": "pin-one"},
    ])
    assert [ref[0] for ref in stage5_before_references(item)] == [
        "pin-one", "pin-two", "pin-three",
    ]


def test_missing_or_malformed_pinterest_snapshot_never_falls_back_to_hat():
    assert stage5_before_references(job(hat={"remote_file_id": "white-hat"})) == []
    assert stage5_before_references(job(references=[
        {"remote_file_id": ""}, {"remote_file_id": "x", "content_type": "text/html"},
        None, "https://example.org/image.jpg",
    ])) == []
