from pathlib import Path
from types import SimpleNamespace

from app.modules.realistic_review_ugc.products_photo_pipeline import (
    PIPELINE_STEPS, readiness,
)




def test_ugc_uses_tenant_assigned_installed_skill_not_obsolete_ugc_v3(tmp_path, monkeypatch):
    from app.modules.realistic_review_ugc import products_photo_pipeline as pipeline
    from app.modules.realistic_review_ugc.model import (
        RrugcStageSkillDefaultModel, RrugcStage2SkillRegistryModel,
    )
    home = tmp_path / "codex"
    runtime = home / "skills" / "ugc-gatorhats"
    runtime.mkdir(parents=True)
    (runtime / "SKILL.md").write_text("---\\nname: ugc-gatorhats\\n---\\n", encoding="utf-8")

    class FakeSession:
        def get(self, model, key):
            if model is RrugcStageSkillDefaultModel:
                if key == ("tenant-a", "prod_s2_ugc"):
                    return SimpleNamespace(registry_id="ugc-registered")
                if key == ("tenant-a", "prod_s2_mh"):
                    return SimpleNamespace(registry_id=None)
            if model is RrugcStage2SkillRegistryModel and key == "ugc-registered":
                return SimpleNamespace(
                    tenant_id="tenant-a", skill_name="ugc-gatorhats",
                    enabled=True, deleted_at=None,
                    validation_status="valid", workflow="image_studio",
                )
            return None

    monkeypatch.setattr(pipeline, "load_codex_skill_manifest",
                        lambda home, name: SimpleNamespace(
                            skill_name=name, workflows=("image_studio",)))
    result = readiness(SimpleNamespace(CODEX_IMAGE_HOME=str(home)),
                       session=FakeSession(), tenant_id="tenant-a")
    ugc = next(step for step in result["steps"] if step["key"] == "ugc")
    assert ugc["skill_name"] == "ugc-gatorhats"
    assert ugc["bundle_installed"] is True
    assert ugc["missing_assets"] == []
    assert ugc["status"] == "needs_pipeline_worker"
    assert ugc["ready"] is False
    # Never report the unassigned Multi-hat slot as installed.
    multi = next(step for step in result["steps"] if step["key"] == "multihat")
    assert multi["bundle_installed"] is False
    assert result["ready"] is False


def test_unavailable_assigned_ugc_fails_closed_without_legacy_missing_files(tmp_path):
    from app.modules.realistic_review_ugc.model import (
        RrugcStageSkillDefaultModel, RrugcStage2SkillRegistryModel,
    )

    class FakeSession:
        def get(self, model, key):
            if model is RrugcStageSkillDefaultModel and key == ("tenant-a", "prod_s2_ugc"):
                return SimpleNamespace(registry_id="ugc-registered")
            if model is RrugcStage2SkillRegistryModel and key == "ugc-registered":
                return SimpleNamespace(tenant_id="tenant-a", skill_name="ugc-gatorhats",
                                       enabled=True, deleted_at=None,
                                       validation_status="valid", workflow="image_studio")
            return None

    result = readiness(SimpleNamespace(CODEX_IMAGE_HOME=str(tmp_path)),
                       session=FakeSession(), tenant_id="tenant-a")
    ugc = next(step for step in result["steps"] if step["key"] == "ugc")
    assert ugc["skill_name"] == "ugc-gatorhats"
    assert not ugc["bundle_installed"]
    assert ugc["status"] == "needs_install"
    assert not any("gatorhats-8869-ugc-v3" in text for text in ugc["missing_assets"])

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
