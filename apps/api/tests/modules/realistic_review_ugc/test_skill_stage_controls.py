from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.realistic_review_ugc import skill_registry, stage_skill_settings, output_versions, stage2_skills
from app.modules.realistic_review_ugc.model import (
    RrugcStage2SkillRegistryModel, RrugcStage2SkillVersionModel,
    RrugcStageSkillDefaultModel, RrugcImageOutputVersionModel,
    RrugcKeywordImageJobModel, RrugcColorwayJobModel, RrugcStage2JobModel,
)


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (AuthAuditEventModel, RrugcStage2SkillRegistryModel, RrugcStage2SkillVersionModel,
                  RrugcStageSkillDefaultModel, RrugcImageOutputVersionModel,
                  RrugcKeywordImageJobModel, RrugcColorwayJobModel, RrugcStage2JobModel):
        model.__table__.create(engine)
    return engine


def _skill(tenant: str, name: str):
    return RrugcStage2SkillRegistryModel(
        tenant_id=tenant, source="local", skill_name=name, display_name=name,
        description="Immutable original description", note="", workflow="image_studio",
        enabled=True, validation_status="valid", sync_state="ready",
    )


def test_note_edit_is_tenant_scoped_and_survives_refresh(monkeypatch):
    engine = _engine()
    try:
        item = stage2_skills.Stage2SkillItem(
            source="local", skill_id=None, skill_name="hat-skill",
            display_name="Hat Skill", description="provider description",
            default_version="1", latest_version="1", local_version="1",
            synced_version="1", ready=True, sync_state="ready", version_options=("1",),
        )
        monkeypatch.setattr(skill_registry, "list_stage2_skill_catalog",
                            lambda refresh=False: stage2_skills.Stage2SkillCatalog(
                                openai_configured=False, openai_status="not_configured",
                                error_code=None, items=(item,)))
        monkeypatch.setattr(skill_registry, "installed_stage2_skill_sha256", lambda *args: "a" * 64)
        with Session(engine) as session:
            skill = _skill("tenant-a", "hat-skill")
            session.add(skill)
            session.commit()
            skill_registry.set_skill_note(session, tenant_id="tenant-a", actor_id="admin",
                                          registry_id=skill.id, note="Produce realistic embroidery")
            assert skill.note == "Produce realistic embroidery"
            assert skill.description == "Immutable original description"
            skill_registry.reconcile_skill_registry(session, tenant_id="tenant-a", refresh=True)
            assert skill.note == "Produce realistic embroidery"
            with pytest.raises(stage2_skills.Stage2SkillRegistryError):
                skill_registry.set_skill_note(session, tenant_id="tenant-b", actor_id="wrong",
                                              registry_id=skill.id, note="overwrite")
            skill_registry.delete_skill(session, tenant_id="tenant-a", actor_id="admin",
                                         registry_id=skill.id)
            assert skill.deleted_at is not None and not skill.enabled
            assert skill_registry.ensure_skill_registry(session, tenant_id="tenant-a", refresh=True) == []
            assert skill_registry.enabled_catalog_items(
                session, tenant_id="tenant-a", catalog_items=(item,)) == ()
    finally:
        engine.dispose()


def test_stage_defaults_independent_and_tenant_scoped(monkeypatch):
    engine = _engine()
    try:
        a = _skill("tenant-a", "colors")
        b = _skill("tenant-a", "studio")
        c = _skill("tenant-b", "other-tenant")
        monkeypatch.setattr(stage_skill_settings, "list_stage2_skill_catalog",
                            lambda refresh=False: SimpleNamespace(items=[
                                SimpleNamespace(source="local", skill_name=name, ready=True)
                                for name in ("colors", "studio", "other-tenant")
                            ]))
        monkeypatch.setattr(stage_skill_settings, "load_codex_skill_manifest",
                            lambda *_args: SimpleNamespace(workflows=("image_studio",),
                                                           required_reference_roles=("artwork",)))
        with Session(engine) as session:
            session.add_all([a, b, c])
            session.commit()
            stage_skill_settings.set_stage_skill_default(
                session, tenant_id="tenant-a", actor_id="admin", stage="stage2", registry_id=a.id)
            stage_skill_settings.set_stage_skill_default(
                session, tenant_id="tenant-a", actor_id="admin", stage="stage4", registry_id=b.id)
            defaults = stage_skill_settings.stage_skill_defaults(session, tenant_id="tenant-a")
            assert defaults == {"stage2": "local:colors", "stage4": "local:studio"}
            assert stage_skill_settings.stage_skill_defaults(session, tenant_id="tenant-b") == {}
            with pytest.raises(stage2_skills.Stage2SkillRegistryError):
                stage_skill_settings.set_stage_skill_default(
                    session, tenant_id="tenant-a", actor_id="admin", stage="stage2", registry_id=c.id)
            with pytest.raises(stage2_skills.Stage2SkillRegistryError):
                stage_skill_settings.set_stage_skill_default(
                    session, tenant_id="tenant-a", actor_id="admin", stage="stage3", registry_id=a.id)
            stage_skill_settings.set_stage_skill_default(
                session, tenant_id="tenant-a", actor_id="admin", stage="stage1", registry_id=a.id)
            assert stage_skill_settings.stage_skill_defaults(session, tenant_id="tenant-a")["stage1"] == "local:colors"
            skill_registry.set_skill_enabled(session, tenant_id="tenant-a", actor_id="admin",
                                             registry_id=a.id, enabled=False)
            assert stage_skill_settings.stage_skill_defaults(session, tenant_id="tenant-a") == {
                "stage4": "local:studio",
            }
    finally:
        engine.dispose()


def test_output_versions_preserve_prior_files_and_tenant_boundary():
    engine = _engine()
    try:
        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            job = SimpleNamespace(id="job-a", output_remote_file_id="old-file",
                                  output_content_type="image/png", output_size_bytes=1024,
                                  output_width=800, output_height=800, completed_at=now)
            version = output_versions.save_output_version(
                session, tenant_id="tenant-a", stage="stage1", job=job,
                remote_file_id="new-file", content_type="image/png", size_bytes=2048,
                width=1024, height=1024, processing_job_id="process-a",
            )
            session.commit()
            assert version == 2
            rows = output_versions.output_versions(session, tenant_id="tenant-a", stage="stage1", job=job)
            assert [item["remote_file_id"] for item in rows] == ["new-file", "old-file"]
            assert output_versions.output_versions(
                session, tenant_id="tenant-b", stage="stage1",
                job=SimpleNamespace(id="job-a", output_remote_file_id=None)) == []
    finally:
        engine.dispose()

def test_stage1_pair_keeps_both_boards_under_one_generation_attempt():
    engine = _engine()
    try:
        with Session(engine) as session:
            job = SimpleNamespace(id="pair-job", output_remote_file_id=None)
            one = output_versions.save_output_version(
                session, tenant_id="tenant-a", stage="stage1", job=job,
                remote_file_id="concepts-board", content_type="image/png",
                size_bytes=1024, width=1024, height=1300, processing_job_id="attempt-a",
            )
            two = output_versions.save_output_version(
                session, tenant_id="tenant-a", stage="stage1", job=job,
                remote_file_id="colorways-board", content_type="image/png",
                size_bytes=2048, width=1400, height=1200, processing_job_id="attempt-a",
                allow_multiple_per_attempt=True,
            )
            session.commit()
            assert (one, two) == (1, 2)
            results = output_versions.output_versions(session, tenant_id="tenant-a",
                                                      stage="stage1", job=job)
            assert [item["remote_file_id"] for item in results] == [
                "colorways-board", "concepts-board",
            ]
            assert [item["processing_job_id"] for item in results] == [
                "attempt-a", "attempt-a",
            ]
    finally:
        engine.dispose()

def test_stage1_keeps_every_generated_image_and_original_filename():
    engine = _engine()
    try:
        with Session(engine) as session:
            job = SimpleNamespace(id="many-output-job", output_remote_file_id=None)
            for index in range(37):
                version = output_versions.save_output_version(
                    session, tenant_id="tenant-a", stage="stage1", job=job,
                    remote_file_id=f"image-{index}", content_type="image/png",
                    size_bytes=1024, width=700, height=500,
                    processing_job_id="attempt-many",
                    allow_multiple_per_attempt=index > 0,
                    output_name=f"v4-job/artworks/concept_{index:02d}/v001.png",
                )
                assert version == index + 1
            session.commit()
            rows = output_versions.output_versions(
                session, tenant_id="tenant-a", stage="stage1", job=job)
            assert len(rows) == 37
            assert rows[0]["output_name"].endswith("concept_36/v001.png")
            assert rows[-1]["remote_file_id"] == "image-0"
            assert len({item["version"] for item in rows}) == 37
    finally:
        engine.dispose()
