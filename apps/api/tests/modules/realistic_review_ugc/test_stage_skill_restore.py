from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.realistic_review_ugc.model import (
    RrugcStage2SkillRegistryModel, RrugcStageSkillDefaultModel,
)
from app.modules.realistic_review_ugc import skill_registry, stage_skill_settings
from app.modules.realistic_review_ugc.stage2_skills import Stage2SkillRegistryError


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    for model in (RrugcStage2SkillRegistryModel, RrugcStageSkillDefaultModel, AuthAuditEventModel):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_restore_archived_keyword_skill_sets_only_stage1_default_and_audits(db, monkeypatch):
    archived = RrugcStage2SkillRegistryModel(
        id="archived-1", tenant_id="tenant-a", source="local",
        skill_name="gatorhats-keyword-embroidery", display_name="Keyword artwork",
        enabled=False, validation_status="valid", sync_state="ready",
        deleted_at=datetime.now(timezone.utc),
    )
    other = RrugcStage2SkillRegistryModel(
        id="other-1", tenant_id="tenant-a", source="local",
        skill_name="other-skill", display_name="Other skill",
    )
    db.add_all([
        archived, other,
        RrugcStageSkillDefaultModel(tenant_id="tenant-a", stage="stage2", registry_id="other-1"),
        RrugcStageSkillDefaultModel(tenant_id="tenant-a", stage="stage4", registry_id="other-1"),
    ])
    db.commit()
    monkeypatch.setattr(skill_registry, "list_stage2_skill_catalog",
        lambda refresh=False: SimpleNamespace(items=(
            SimpleNamespace(source="local", skill_name="gatorhats-keyword-embroidery", ready=True),
        )))
    monkeypatch.setattr(stage_skill_settings, "keyword_skill_compatible", lambda _name: True)
    restored = skill_registry.restore_keyword_skill(
        db, tenant_id="tenant-a", actor_id="admin-a", registry_id="archived-1",
    )
    assert restored.deleted_at is None and restored.enabled is True
    defaults = {
        item.stage: item.registry_id
        for item in db.scalars(select(RrugcStageSkillDefaultModel))
    }
    assert defaults == {"stage1": "archived-1", "stage2": "other-1", "stage4": "other-1"}
    actions = [row.action for row in db.scalars(select(AuthAuditEventModel))]
    assert "stage2_skill.restored" in actions
    assert "rrugc.stage_skill.default_updated" in actions
    with pytest.raises(Stage2SkillRegistryError) as exc:
        skill_registry.restore_keyword_skill(
            db, tenant_id="tenant-a", actor_id="admin-a", registry_id="archived-1",
        )
    assert exc.value.code == "stage_skill_not_archived"
    with pytest.raises(Stage2SkillRegistryError) as exc:
        skill_registry.restore_keyword_skill(
            db, tenant_id="tenant-b", actor_id="admin-a", registry_id="other-1",
        )
    assert exc.value.status_code == 404


def test_restore_rejects_skill_without_keyword_only_capability(db, monkeypatch):
    db.add(RrugcStage2SkillRegistryModel(
        id="archived-2", tenant_id="tenant-a", source="local",
        skill_name="concept-only", display_name="Concept-only",
        enabled=False, deleted_at=datetime.now(timezone.utc),
    ))
    db.commit()
    monkeypatch.setattr(skill_registry, "list_stage2_skill_catalog",
        lambda refresh=False: SimpleNamespace(items=(
            SimpleNamespace(source="local", skill_name="concept-only", ready=True),
        )))
    monkeypatch.setattr(stage_skill_settings, "keyword_skill_compatible", lambda _name: False)
    with pytest.raises(Stage2SkillRegistryError) as exc:
        skill_registry.restore_keyword_skill(
            db, tenant_id="tenant-a", actor_id="admin-a", registry_id="archived-2",
        )
    assert exc.value.code == "stage_skill_restore_incompatible"
    db.refresh(db.get(RrugcStage2SkillRegistryModel, "archived-2"))
    assert db.get(RrugcStage2SkillRegistryModel, "archived-2").deleted_at is not None
