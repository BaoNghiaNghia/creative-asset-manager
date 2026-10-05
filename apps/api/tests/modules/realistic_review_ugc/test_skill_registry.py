from __future__ import annotations

import io
import json
import zipfile

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.realistic_review_ugc import skill_registry, stage2_skills
from app.modules.realistic_review_ugc.model import (
    RrugcStage2SkillRegistryModel,
    RrugcStage2SkillVersionModel,
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
