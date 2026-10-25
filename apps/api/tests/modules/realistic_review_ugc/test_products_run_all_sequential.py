"""Seven-slot Run all must queue one slot at a time and survive page closure."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.realistic_review_ugc.cap_stack_model import RrugcCapStackImageModel
from app.modules.realistic_review_ugc.colorways import PRODUCTS_COLORS, PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.model import (
    RrugcSourcePlanModel, RrugcProductsRunAllModel,
    RrugcEcommerceJobModel, RrugcColorwayJobModel,
)
from app.modules.realistic_review_ugc.products_run_all import ProductsRunAllService, ProductsRunAllScheduler, SLOT_GROUPS
from app.modules.realistic_review_ugc.stage2_skills import ResolvedStage2Skill

TENANT = "tenant-001"
DESIGN = "plan-001"
COLOR = "natural-charcoal"
SKILL = {
    "source": "local", "skill_id": None,
    "skill_name": "s7-8869-scale-12-color", "skill_version": None,
}
SETTINGS = SimpleNamespace(
    PROCESSING_JOBS_ENABLED=True, IMAGE_GENERATION_ENABLED=True,
    MANAGED_ASSET_STORAGE_ENABLED=True, CODEX_IMAGE_GENERATION_ENABLED=True,
)


@pytest.fixture
def context(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        RrugcSourcePlanModel.__table__, RrugcProductsRunAllModel.__table__,
        RrugcEcommerceJobModel.__table__, RrugcColorwayJobModel.__table__,
        ProcessingJobModel.__table__, TenantProcessingPolicyModel.__table__,
        RrugcCapStackImageModel.__table__,
    ])
    import app.modules.realistic_review_ugc.ecommerce_pipeline as ep
    import app.modules.realistic_review_ugc.products_run_all as mod

    monkeypatch.setattr(ep, "_stock_refs", lambda *_args: ([("stock_front", b"stock")], "a" * 64))
    monkeypatch.setattr(ep, "_assigned_skill", lambda *_args: ResolvedStage2Skill(
        source="local", skill_id=None, skill_name="8869-fine-thread", skill_version=None))
    monkeypatch.setattr(ep, "installed_stage2_skill_sha256", lambda *args, **kwargs: "f" * 64)

    stage_calls: list[str] = []
    color_calls: list[dict] = []

    def fake_scene(self, *, tenant_id, user_id, source_plan_id, slot, color_key):
        stage_calls.append(slot)
        for stage in ep.SCENE_GROUPS[slot]:
            found = self.session.scalar(select(RrugcEcommerceJobModel).where(
                RrugcEcommerceJobModel.stage == stage,
                RrugcEcommerceJobModel.source_plan_id == source_plan_id,
                RrugcEcommerceJobModel.color_key == color_key,
            ))
            if found:
                continue
            self.session.add(RrugcEcommerceJobModel(
                id=str(uuid4()), tenant_id=tenant_id, source_plan_id=source_plan_id,
                source_revision="rev1", stage=stage, color_key=color_key,
                stock_sha256="a" * 64, skill_source="local",
                skill_name="scene-skill", created_by_user_id=user_id, status="queued",
            ))
        self.session.commit()
        return {"steps": [{"stage": stage, "status": "queued"} for stage in ep.SCENE_GROUPS[slot]]}

    def fake_color(self, *, tenant_id, user_id, source_plan_ids, workflow,
                   skill_source, skill_id, skill_name, skill_version):
        color_calls.append({"source": skill_source, "skill_name": skill_name})
        for key, name in PRODUCTS_COLORS:
            found = self.session.scalar(select(RrugcColorwayJobModel).where(
                RrugcColorwayJobModel.color_key == key,
                RrugcColorwayJobModel.source_plan_id == source_plan_ids[0],
            ))
            if found:
                continue
            self.session.add(RrugcColorwayJobModel(
                id=str(uuid4()), tenant_id=tenant_id,
                source_plan_id=source_plan_ids[0], source_revision="rev1",
                color_key=key, stock_sha256="c" * 64,
                skill_source=skill_source, skill_id=skill_id,
                skill_name=skill_name, skill_version=skill_version,
                created_by_user_id=user_id, status="queued",
            ))
        self.session.commit()
        return {"queued": 12, "existing": 0}

    monkeypatch.setattr(ep.EcommerceService, "queue_scene", fake_scene)
    monkeypatch.setattr(mod.ColorwayService, "queue", fake_color)

    with Session(engine) as session:
        session.add(RrugcSourcePlanModel(
            id=DESIGN, tenant_id=TENANT, root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="source-file", source_relative_path="embroidery.png",
            source_name="embroidery.png", source_mime_type="image/png",
            source_revision="rev1", created_by_user_id="operator",
        ))
        session.commit()
        yield session, ProductsRunAllService(session, SETTINGS), stage_calls, color_calls
    engine.dispose()


def ecommerce_jobs(session, position):
    return list(session.scalars(select(RrugcEcommerceJobModel).where(
        RrugcEcommerceJobModel.stage.in_(SLOT_GROUPS[position - 1]))))


def done(session, position):
    if position < 7:
        jobs = ecommerce_jobs(session, position)
    else:
        jobs = list(session.scalars(select(RrugcColorwayJobModel)))
    assert len(jobs) == len(SLOT_GROUPS[position - 1])
    for row in jobs:
        row.status = "completed"
        row.output_remote_file_id = f"saved-{row.id}"
        if row.processing_job_id:
            processing = session.get(ProcessingJobModel, row.processing_job_id)
            if processing:
                processing.status = "completed"
    session.commit()


def test_run_all_never_fans_out_to_next_slot_before_completion(context):
    session, service, scene_calls, color_calls = context
    start = service.start(
        tenant_id=TENANT, user_id="operator", source_plan_id=DESIGN,
        color_key=COLOR, colorway_skill=SKILL,
    )
    assert start["status"] == "running"
    assert start["current_slot"] == 1
    assert len(ecommerce_jobs(session, 1)) == 1
    assert ecommerce_jobs(session, 2) == []
    assert ecommerce_jobs(session, 4) == []
    run_id = start["run_id"]

    # Re-click and restart with a newly constructed service must stay idempotent.
    same = ProductsRunAllService(session, SETTINGS).start(
        tenant_id=TENANT, user_id="operator", source_plan_id=DESIGN,
        color_key=COLOR, colorway_skill=SKILL,
    )
    assert same["run_id"] == run_id
    assert len(ecommerce_jobs(session, 1)) == 1

    for position in range(1, 7):
        assert service.status(
            tenant_id=TENANT, source_plan_id=DESIGN, color_key=COLOR,
        )["current_slot"] == position
        done(session, position)
        result = service.advance(run_id)
        assert result["current_slot"] == position + 1
        if position < 6:
            assert len(ecommerce_jobs(session, position + 1)) == len(SLOT_GROUPS[position])
        assert result["status"] == "running"
        assert scene_calls == (["prod_s2_mh"] if position == 4 else
                               ["prod_s2_mh", "prod_s2_ugc"] if position >= 5 else [])
        if position < 6:
            assert color_calls == []

    assert scene_calls == ["prod_s2_mh", "prod_s2_ugc"]
    assert color_calls == [{"source": "local", "skill_name": SKILL["skill_name"]}]
    assert session.query(RrugcColorwayJobModel).count() == 12
    done(session, 7)
    finished = service.advance(run_id)
    assert finished["status"] == "completed"
    assert finished["current_slot"] == 7
    persisted = service.status(tenant_id=TENANT, source_plan_id=DESIGN, color_key=COLOR)
    assert all(step["status"] == "completed" for step in persisted["steps"])
    assert service.start(tenant_id=TENANT, user_id="operator", source_plan_id=DESIGN,
                         color_key=COLOR, colorway_skill=SKILL)["status"] == "completed"
    assert len(ecommerce_jobs(session, 1)) == 1


def test_failures_halt_sequence_until_manual_retry(context):
    session, service, scenes, _color = context
    started = service.start(tenant_id=TENANT, user_id="operator", source_plan_id=DESIGN,
                            color_key=COLOR, colorway_skill=SKILL)
    run_id = started["run_id"]
    done(session, 1)
    service.advance(run_id)
    assert len(ecommerce_jobs(session, 2)) == 1
    failed = ecommerce_jobs(session, 2)[0]
    failed.status = "failed"
    processing = session.get(ProcessingJobModel, failed.processing_job_id)
    processing.status = "failed"
    session.commit()
    blocked = service.advance(run_id)
    assert blocked["status"] == "blocked"
    assert blocked["current_slot"] == 2
    assert ecommerce_jobs(session, 3) == []
    # Re-click does not spend credits / silently retry a failed job.
    again = service.start(tenant_id=TENANT, user_id="operator", source_plan_id=DESIGN,
                          color_key=COLOR, colorway_skill=SKILL)
    assert again["status"] == "blocked"
    assert failed.retry_count == 0
    assert ecommerce_jobs(session, 3) == []
    assert scenes == []

    # Explicit manual retry is allowed, then a new click continues the run.
    failed.status = "queued"
    processing.status = "pending"
    session.commit()
    resumed = service.start(tenant_id=TENANT, user_id="operator", source_plan_id=DESIGN,
                            color_key=COLOR, colorway_skill=SKILL)
    assert resumed["status"] == "running"
    assert resumed["current_slot"] == 2
    assert ecommerce_jobs(session, 3) == []
    done(session, 2)
    assert service.advance(run_id)["current_slot"] == 3
    assert len(ecommerce_jobs(session, 3)) == 1


def test_source_revision_change_blocks_old_sequence_without_new_jobs(context):
    session, service, _scenes, _color = context
    started = service.start(tenant_id=TENANT, user_id="operator",
                            source_plan_id=DESIGN, color_key=COLOR, colorway_skill=SKILL)
    plan = session.get(RrugcSourcePlanModel, DESIGN)
    plan.source_revision = "rev2"
    session.commit()
    result = service.advance(started["run_id"])
    assert result["status"] == "blocked"
    assert len(ecommerce_jobs(session, 2)) == 0


def test_no_automatic_run_all_when_not_explicitly_started(context):
    session, service, _scenes, _color = context
    assert service.status(tenant_id=TENANT, source_plan_id=DESIGN, color_key=COLOR) is None
    assert session.query(RrugcProductsRunAllModel).count() == 0
    assert session.query(RrugcEcommerceJobModel).count() == 0

def test_scheduler_recovers_after_committed_output_without_worker_callback(context):
    session, service, _scenes, _colors = context
    result = service.start(tenant_id=TENANT, user_id="operator",
                           source_plan_id=DESIGN, color_key=COLOR, colorway_skill=SKILL)
    done(session, 1)
    # Simulate a process termination after storage commit but before the
    # handler could invoke advance_after_output. No browser is involved.
    scheduler = ProductsRunAllScheduler(lambda: Session(session.get_bind()), SETTINGS)
    assert scheduler.tick() == 1
    resumed = service.status(tenant_id=TENANT, source_plan_id=DESIGN, color_key=COLOR)
    assert resumed["run_id"] == result["run_id"]
    assert resumed["current_slot"] == 2
    assert len(ecommerce_jobs(session, 2)) == 1
    # Subsequent reconciliation cannot duplicate the image request.
    assert scheduler.tick() == 1
    assert len(ecommerce_jobs(session, 2)) == 1


def test_partial_scene_queue_repair_stays_within_same_slot(context):
    session, service, _scenes, _colors = context
    result = service.start(tenant_id=TENANT, user_id="operator",
                           source_plan_id=DESIGN, color_key=COLOR, colorway_skill=SKILL)
    for position in range(1, 5):
        done(session, position)
        service.advance(result["run_id"])
    assert len(ecommerce_jobs(session, 5)) == 3
    removed = ecommerce_jobs(session, 5)[-1]
    session.delete(removed)
    session.commit()
    # Two scene jobs still queued, but third disappeared during partial commit:
    # queue only the missing scene and never start UGC prematurely.
    assert service.advance(result["run_id"])["current_slot"] == 5
    assert len(ecommerce_jobs(session, 5)) == 3
    assert ecommerce_jobs(session, 6) == []