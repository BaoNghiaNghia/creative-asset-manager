from pathlib import Path
from types import SimpleNamespace

from app.modules.realistic_review_ugc.products_photo_pipeline import (
    PIPELINE_STEPS, readiness,
)


def test_three_skill_photography_pipeline_is_fail_closed(tmp_path: Path):
    result = readiness(SimpleNamespace(CODEX_IMAGE_HOME=str(tmp_path)))
    assert result["ready"] is False
    assert result["sequential"] is True
    assert result["total_outputs_per_design"] == 10
    assert [step["key"] for step in result["steps"]] == [
        "ecommerce", "multihat", "ugc",
    ]
    assert [step["output_count"] for step in result["steps"]] == [4, 3, 3]
    assert all(not step["ready"] and not step["bundle_installed"] for step in result["steps"])
    assert result["steps"][2]["skill_name"] == "gatorhats-8869-ugc-v3"


def test_bundle_files_preflight_does_not_falsely_claim_worker_ready(tmp_path: Path):
    root = tmp_path / "skills"
    for spec in PIPELINE_STEPS:
        for rel in spec["required_assets"]:
            target = root / spec["skill_name"] / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"some nonsecret input")
    result = readiness(SimpleNamespace(CODEX_IMAGE_HOME=str(tmp_path)))
    assert all(step["bundle_installed"] for step in result["steps"])
    assert all(step["status"] == "needs_pipeline_worker" for step in result["steps"])
    assert result["ready"] is False
