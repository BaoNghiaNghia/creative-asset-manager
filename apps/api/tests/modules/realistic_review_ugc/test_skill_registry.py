from __future__ import annotations

import io
import json
import zipfile

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.realistic_review_ugc import skill_registry, stage2_skills
from app.modules.realistic_review_ugc.model import (
    RrugcStage2SkillRegistryModel,
    RrugcStage2SkillVersionModel,
    RrugcStageSkillDefaultModel,
)


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AuthAuditEventModel.__table__.create(engine)
    RrugcStage2SkillRegistryModel.__table__.create(engine)
    RrugcStage2SkillVersionModel.__table__.create(engine)
    RrugcStageSkillDefaultModel.__table__.create(engine)
    return engine


def _catalog_item() -> stage2_skills.Stage2SkillItem:
    return stage2_skills.Stage2SkillItem(
        source="local",
        skill_id=None,
        skill_name="test-image-studio",
        display_name="Test Image Studio",
        description="test",
        default_version="1",
        latest_version="1",
        local_version="1",
        synced_version="1",
        ready=True,
        sync_state="ready",
        version_options=("1",),
    )


def test_registry_reconcile_and_disable_filters_stage2_catalog(monkeypatch):
    engine = _engine()
    try:
        item = _catalog_item()
        monkeypatch.setattr(
            skill_registry,
            "list_stage2_skill_catalog",
            lambda refresh=False: stage2_skills.Stage2SkillCatalog(
                openai_configured=False,
                openai_status="not_configured",
                error_code=None,
                items=(item,),
            ),
        )
        monkeypatch.setattr(
            skill_registry,
            "installed_stage2_skill_sha256",
            lambda _name: "a" * 64,
        )
        with Session(engine) as session:
            rows = skill_registry.reconcile_skill_registry(
                session,
                tenant_id="tenant-a",
                refresh=True,
            )
            assert len(rows) == 1
            assert rows[0].enabled is True
            assert rows[0].synced_version == "1"
            assert skill_registry.enabled_catalog_items(
                session,
                tenant_id="tenant-a",
                catalog_items=(item,),
            ) == (item,)

            skill_registry.set_skill_enabled(
                session,
                tenant_id="tenant-a",
                actor_id="admin-a",
                registry_id=rows[0].id,
                enabled=False,
            )
            assert skill_registry.enabled_catalog_items(
                session,
                tenant_id="tenant-a",
                catalog_items=(item,),
            ) == ()
            audit = session.scalar(
                select(AuthAuditEventModel).where(
                    AuthAuditEventModel.action == "stage2_skill.disabled"
                )
            )
            assert audit is not None
            assert audit.actor_id == "admin-a"
    finally:
        engine.dispose()


def test_uploaded_skill_bundle_is_validated_and_hashed():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "SKILL.md",
            "---\n"
            "name: test-image-studio\n"
            "version: 2\n"
            "description: Test image generation skill\n"
            "---\n"
            "# Test\n",
        )
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "skill_name": "test-image-studio",
                    "display_name": "Test Image Studio",
                    "description": "Test image generation skill",
                    "workflows": ["image_studio"],
                }
            ),
        )
    bundle = stage2_skills.inspect_uploaded_stage2_skill_bundle(buffer.getvalue())
    assert bundle.skill_name == "test-image-studio"
    assert bundle.display_name == "Test Image Studio"
    assert bundle.version == "2"
    assert len(bundle.bundle_sha256) == 64
    assert {name for name, _body, _content_type in bundle.files} == {
        "SKILL.md",
        "manifest.json",
    }


def test_create_skill_installs_local_runtime_without_openai_key(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "SKILL.md",
            "---\n"
            "name: uploaded-image-studio\n"
            "version: 1.4.0\n"
            "description: Uploaded local image generation skill\n"
            "---\n"
            "# Uploaded skill\n",
        )
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "skill_name": "uploaded-image-studio",
                    "display_name": "Uploaded Image Studio",
                    "description": "Uploaded local image generation skill",
                    "workflows": ["image_studio"],
                }
            ),
        )

    settings = Settings(
        _env_file=None,
        CODEX_IMAGE_HOME=str(tmp_path / "codex"),
        OPENAI_API_KEY="",
    )
    monkeypatch.setattr(stage2_skills, "get_settings", lambda: settings)

    engine = _engine()
    try:
        with Session(engine) as session:
            row = skill_registry.create_skill(
                session,
                tenant_id="tenant-a",
                actor_id="admin-a",
                bundle_bytes=buffer.getvalue(),
            )
            assert row.source == "local"
            assert row.skill_id is None
            assert row.skill_name == "uploaded-image-studio"
            assert row.sync_state == "ready"
            assert row.validation_status == "valid"
            assert row.default_version == "1.4.0"
            assert row.synced_version == "1.4.0"
            assert len(row.bundle_sha256 or "") == 64

            installed = tmp_path / "codex" / "skills" / "uploaded-image-studio"
            assert (installed / "SKILL.md").is_file()
            assert (installed / "manifest.json").is_file()
            assert not (installed / ".openai-skill.json").exists()

            version = session.scalar(
                select(RrugcStage2SkillVersionModel).where(
                    RrugcStage2SkillVersionModel.registry_id == row.id,
                    RrugcStage2SkillVersionModel.version == "1.4.0",
                )
            )
            assert version is not None
            assert version.is_synced is True
    finally:
        engine.dispose()


def _skill_zip(version: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", (
            f"---\nname: existing-studio\nversion: {version}\n"
            f"description: Bundle {version}\n---\n# Studio\n"
        ))
        archive.writestr("manifest.json", json.dumps({
            "schema_version": 1, "skill_name": "existing-studio",
            "display_name": f"Studio {version}", "description": f"Bundle {version}",
            "workflows": ["image_studio"],
        }))
    return buffer.getvalue()


def test_duplicate_zip_replace_preserves_defaults_notes_and_versions(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, CODEX_IMAGE_HOME=str(tmp_path / "codex"), OPENAI_API_KEY="")
    monkeypatch.setattr(stage2_skills, "get_settings", lambda: settings)
    monkeypatch.setattr(skill_registry, "_count_active_local_skill_jobs", lambda *_a, **_kw: 0)
    first, second = _skill_zip("1.0"), _skill_zip("2.0")
    engine = _engine()
    try:
        with Session(engine) as session:
            row = skill_registry.create_skill(
                session, tenant_id="tenant-a", actor_id="admin", bundle_bytes=first,
            )
            registry_id, old_sha = row.id, row.bundle_sha256
            row.note, row.enabled = "Use in Stage 2", False
            session.add(RrugcStageSkillDefaultModel(
                tenant_id="tenant-a", stage="stage2", registry_id=row.id,
            ))
            session.commit()
            installed = tmp_path / "codex" / "skills" / "existing-studio" / "SKILL.md"
            with pytest.raises(stage2_skills.Stage2SkillRegistryError) as error:
                skill_registry.create_skill(
                    session, tenant_id="tenant-a", actor_id="admin", bundle_bytes=second,
                )
            assert error.value.code == "stage2_skill_already_exists"
            assert "version: 1.0" in installed.read_text()

            updated = skill_registry.create_skill(
                session, tenant_id="tenant-a", actor_id="admin",
                bundle_bytes=second, replace_existing=True,
            )
            assert updated.id == registry_id
            assert updated.note == "Use in Stage 2" and updated.enabled is False
            assert updated.synced_version == "2.0" and updated.display_name == "Studio 2.0"
            assert updated.bundle_sha256 != old_sha
            assert "version: 2.0" in installed.read_text()
            assert session.get(RrugcStageSkillDefaultModel, ("tenant-a", "stage2")).registry_id == registry_id
            assert len(list(session.scalars(select(RrugcStage2SkillRegistryModel)))) == 1
            audits = lambda: list(session.scalars(
                select(AuthAuditEventModel).where(
                    AuthAuditEventModel.action == "stage2_skill.local_bundle_updated",
                )
            ))
            assert len(audits()) == 1
            skill_registry.create_skill(
                session, tenant_id="tenant-a", actor_id="admin",
                bundle_bytes=second, replace_existing=True,
            )
            assert len(audits()) == 1
    finally:
        engine.dispose()


def test_replace_rejects_active_hosted_and_archived_skills(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, CODEX_IMAGE_HOME=str(tmp_path / "codex"), OPENAI_API_KEY="")
    monkeypatch.setattr(stage2_skills, "get_settings", lambda: settings)
    monkeypatch.setattr(skill_registry, "_count_active_local_skill_jobs", lambda *_a, **_kw: 0)
    engine = _engine()
    try:
        with Session(engine) as session:
            row = skill_registry.create_skill(
                session, tenant_id="tenant-a", actor_id="admin",
                bundle_bytes=_skill_zip("1.0"),
            )
            installed = tmp_path / "codex" / "skills" / "existing-studio" / "SKILL.md"
            monkeypatch.setattr(skill_registry, "_count_active_local_skill_jobs", lambda *_a, **_kw: 1)
            with pytest.raises(stage2_skills.Stage2SkillRegistryError) as error:
                skill_registry.create_skill(
                    session, tenant_id="tenant-a", actor_id="admin",
                    bundle_bytes=_skill_zip("2.0"), replace_existing=True,
                )
            assert error.value.code == "stage_skill_update_active_jobs"
            monkeypatch.setattr(skill_registry, "_count_active_local_skill_jobs", lambda *_a, **_kw: 0)

            row.source = "openai"
            session.commit()
            with pytest.raises(stage2_skills.Stage2SkillRegistryError) as error:
                skill_registry.create_skill(
                    session, tenant_id="tenant-a", actor_id="admin",
                    bundle_bytes=_skill_zip("2.0"), replace_existing=True,
                )
            assert error.value.code == "stage2_skill_hosted_name_conflict"
            row.source = "local"
            row.deleted_at = skill_registry.utcnow()
            row.enabled = False
            session.commit()
            # A previously deleted local skill is restored only after explicit
            # replacement confirmation; old registry ID and outputs survive.
            restored = skill_registry.create_skill(
                session, tenant_id="tenant-a", actor_id="admin",
                bundle_bytes=_skill_zip("2.0"), replace_existing=True,
            )
            assert restored.id == row.id
            assert restored.deleted_at is None and restored.enabled is True
            assert "version: 2.0" in installed.read_text()
    finally:
        engine.dispose()
