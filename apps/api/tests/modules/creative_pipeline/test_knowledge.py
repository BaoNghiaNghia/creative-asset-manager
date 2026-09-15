import json
from pathlib import Path

import pytest

from app.modules.creative_pipeline.knowledge import (
    KnowledgeLoader, KnowledgePackError, KnowledgeStage, normalize_text,
)


def test_pack_loads_and_stage_selection_is_platform_specific():
    loader = KnowledgeLoader()
    etsy = loader.select_files("etsy", KnowledgeStage.IDEA_STORY)
    amazon = loader.select_files("amazon", KnowledgeStage.IDEA_STORY)
    assert etsy == ("global_rules.md", "etsy_rules.md", "idea_story_rules.md", "hooks_library.md")
    assert amazon == ("global_rules.md", "amazon_rules.md", "idea_story_rules.md", "hooks_library.md")
    assert "amazon_rules.md" not in etsy
    assert "etsy_rules.md" not in amazon


def test_snapshot_hash_is_deterministic_and_normalized(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "global_rules.md").write_bytes(b"caf\xc3\xa9\r\n")
    (root / "etsy_rules.md").write_text("etsy\n", encoding="utf-8")
    (root / "idea_story_rules.md").write_text("idea\n", encoding="utf-8")
    (root / "hooks_library.md").write_text("hooks\n", encoding="utf-8")
    (root / "seedance_2_5_rules.md").write_text("seed\n", encoding="utf-8")
    (root / "google_omni_rules.md").write_text("omni\n", encoding="utf-8")
    manifest = {"schema_version": 1, "pack_version": "1", "stages": {
        "idea_story": ["global_rules.md", "{platform}_rules.md", "idea_story_rules.md", "hooks_library.md"],
        "seedance_2_5": ["global_rules.md", "{platform}_rules.md", "seedance_2_5_rules.md"],
        "google_omni": ["global_rules.md", "{platform}_rules.md", "google_omni_rules.md"],
    }, "examples": []}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    a = KnowledgeLoader(root).create_run_snapshot("etsy")
    (root / "manifest.json").write_text(json.dumps({**manifest, "stages": {k: list(reversed(v)) for k, v in manifest["stages"].items()}}, sort_keys=True), encoding="utf-8")
    b = KnowledgeLoader(root).create_run_snapshot("etsy")
    assert a.snapshot_id == b.snapshot_id
    assert normalize_text(bytes.fromhex("636166c3a9 0d0a")) == normalize_text(bytes.fromhex("636166c3a9 0a"))
    (root / "idea_story_rules.md").write_text("idea changed\n", encoding="utf-8")
    assert KnowledgeLoader(root).create_run_snapshot("etsy").snapshot_id != a.snapshot_id


@pytest.mark.parametrize("bad", [
    {"schema_version": 2},
])
def test_manifest_rejects_invalid_schema(tmp_path, bad):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(KnowledgePackError):
        KnowledgeLoader(root).load_manifest()


def test_manifest_rejects_traversal_and_unknown_stage(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    files = ["global_rules.md", "etsy_rules.md", "idea_story_rules.md", "hooks_library.md", "seedance_2_5_rules.md", "google_omni_rules.md"]
    for name in files: (root / name).write_text("ok", encoding="utf-8")
    manifest = {"schema_version": 1, "pack_version": "1", "stages": {
        "idea_story": ["../global_rules.md", "{platform}_rules.md", "idea_story_rules.md", "hooks_library.md"],
        "seedance_2_5": ["global_rules.md", "{platform}_rules.md", "seedance_2_5_rules.md"],
        "google_omni": ["global_rules.md", "{platform}_rules.md", "google_omni_rules.md"],
    }, "examples": []}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(KnowledgePackError): KnowledgeLoader(root).load_manifest()
    manifest["stages"]["idea_story"][0] = "global_rules.md"
    manifest["stages"]["unknown"] = manifest["stages"].pop("google_omni")
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(KnowledgePackError): KnowledgeLoader(root).load_manifest()


def test_unreferenced_file_does_not_change_snapshot():
    loader = KnowledgeLoader()
    before = loader.create_run_snapshot("etsy").snapshot_id
    extra = loader.root / "unreferenced.tmp"
    extra.write_text("must not be loaded", encoding="utf-8")
    try:
        assert loader.create_run_snapshot("etsy").snapshot_id == before
    finally:
        extra.unlink()
