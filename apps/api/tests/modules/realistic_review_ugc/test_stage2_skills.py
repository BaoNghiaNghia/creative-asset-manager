from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.modules.realistic_review_ugc import stage2_skills


def _settings(tmp_path, *, api_key=None):
    return SimpleNamespace(
        CODEX_IMAGE_HOME=str(tmp_path / "codex"),
        OPENAI_API_KEY=api_key,
        OPENAI_BASE_URL=None,
        OPENAI_ORGANIZATION=None,
        OPENAI_PROJECT=None,
        OPENAI_TIMEOUT_SECONDS=5.0,
        OPENAI_MAX_RETRIES=0,
    )


def _write_image_studio_skill(tmp_path, name="gatorhats-8869-image-studio", version="3.0.0"):
    root = tmp_path / "codex" / "skills" / name
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"version: {version}\n"
        "description: test skill\n"
        "---\n"
        "# Test skill\n",
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "skill_name": name,
                "display_name": "Test Image Studio",
                "description": "test",
                "workflows": ["image_studio"],
                "product_types": [],
                "required_reference_roles": [],
                "optional_reference_roles": [],
                "max_references": 10,
            }
        ),
        encoding="utf-8",
    )
    return root


def test_stage2_skill_catalog_uses_local_fallback_without_openai(tmp_path):
    _write_image_studio_skill(tmp_path)
    settings = _settings(tmp_path)

    catalog = stage2_skills.list_stage2_skill_catalog(settings, refresh=True)

    assert catalog.openai_configured is False
    assert catalog.openai_status == "not_configured"
    assert [item.skill_name for item in catalog.items] == [
        "gatorhats-8869-image-studio"
    ]
    item = catalog.items[0]
    assert item.source == "local"
    assert item.ready is True
    assert item.synced_version == "3.0.0"
    assert item.version_options == ("3.0.0",)


def test_stage2_remote_skill_keeps_synced_version_after_default_changes(tmp_path):
    root = _write_image_studio_skill(tmp_path, name="remote-hat-skill", version="4")
    (root / ".openai-skill.json").write_text(
        json.dumps(
            {
                "skill_id": "skill_123",
                "skill_name": "remote-hat-skill",
                "version": "4",
            }
        ),
        encoding="utf-8",
    )
    settings = _settings(tmp_path, api_key="test-key")
    local = stage2_skills._local_manifests(settings)

    item = stage2_skills._remote_item(
        settings,
        {
            "id": "skill_123",
            "name": "remote-hat-skill",
            "description": "remote test",
            "default_version": "6",
            "latest_version": "7",
        },
        local,
    )

    assert item is not None
    assert item.ready is True
    assert item.sync_state == "update_available"
    assert item.synced_version == "4"
    assert item.version_options == ("6", "7", "4")


def test_stage2_unsynced_openai_skill_cannot_queue(tmp_path, monkeypatch):
    settings = _settings(tmp_path, api_key="test-key")
    stage2_skills._catalog_cache.clear()
    monkeypatch.setattr(
        stage2_skills,
        "_fetch_openai_skills",
        lambda _settings: [
            {
                "id": "skill_456",
                "name": "remote-hat-skill",
                "description": "remote test",
                "default_version": "2",
                "latest_version": "2",
            }
        ],
    )

    catalog = stage2_skills.list_stage2_skill_catalog(settings, refresh=True)
    assert len(catalog.items) == 1
    assert catalog.items[0].sync_state == "not_synced"
    assert catalog.items[0].ready is False

    with pytest.raises(stage2_skills.Stage2SkillRegistryError) as exc_info:
        stage2_skills.resolve_stage2_skill(
            settings=settings,
            skill_source="openai",
            skill_id="skill_456",
            skill_name="remote-hat-skill",
            skill_version="2",
            fallback_skill_name="gatorhats-8869-image-studio",
        )

    assert exc_info.value.code == "stage2_skill_not_synced"


def test_verify_stage2_local_skill_runtime_rejects_version_drift(tmp_path):
    _write_image_studio_skill(tmp_path, version="3.0.0")
    settings = _settings(tmp_path)

    stage2_skills.verify_stage2_skill_runtime(
        settings=settings,
        skill_source="local",
        skill_id=None,
        skill_name="gatorhats-8869-image-studio",
        skill_version="3.0.0",
    )

    with pytest.raises(stage2_skills.Stage2SkillRegistryError) as exc_info:
        stage2_skills.verify_stage2_skill_runtime(
            settings=settings,
            skill_source="local",
            skill_id=None,
            skill_name="gatorhats-8869-image-studio",
            skill_version="2.9.0",
        )

    assert exc_info.value.code == "stage2_skill_runtime_version_mismatch"


def test_verify_stage2_openai_skill_runtime_rejects_id_or_version_drift(tmp_path):
    root = _write_image_studio_skill(tmp_path, name="remote-hat-skill", version="4")
    (root / ".openai-skill.json").write_text(
        json.dumps(
            {
                "skill_id": "skill_123",
                "skill_name": "remote-hat-skill",
                "version": "4",
            }
        ),
        encoding="utf-8",
    )
    settings = _settings(tmp_path, api_key="test-key")

    stage2_skills.verify_stage2_skill_runtime(
        settings=settings,
        skill_source="openai",
        skill_id="skill_123",
        skill_name="remote-hat-skill",
        skill_version="4",
    )

    with pytest.raises(stage2_skills.Stage2SkillRegistryError) as exc_info:
        stage2_skills.verify_stage2_skill_runtime(
            settings=settings,
            skill_source="openai",
            skill_id="skill_other",
            skill_name="remote-hat-skill",
            skill_version="4",
        )
    assert exc_info.value.code == "stage2_skill_runtime_id_mismatch"

    with pytest.raises(stage2_skills.Stage2SkillRegistryError) as exc_info:
        stage2_skills.verify_stage2_skill_runtime(
            settings=settings,
            skill_source="openai",
            skill_id="skill_123",
            skill_name="remote-hat-skill",
            skill_version="5",
        )
    assert exc_info.value.code == "stage2_skill_runtime_version_mismatch"
