"""Phase 3B: durable vote outbox, bounded reconciliation and stale invalidation."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.agent_harness.feedback_outbox import (
    EVENT_TYPE, MAX_EVENTS_PER_TICK, capture_if_enabled, drain_feedback_events,
    record_stage1_feedback_change,
)
from app.modules.agent_harness.feedback_scheduler import AgentFeedbackOutboxScheduler
from app.modules.agent_harness.model import AgentDefinitionModel, AgentExperienceModel
from app.modules.agent_harness.registry import AgentDefinition
from app.modules.agent_harness.repository import AgentHarnessRepository
from app.modules.agent_harness.stage1_feedback import snapshots
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.processing.model import OutboxEventModel
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel as Output,
    RrugcKeywordImageJobModel as ImageJob,
    RrugcSourcePlanModel,
)
from app.modules.realistic_review_ugc.stage1_design_learning import set_design_feedback


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[
        Output.__table__, ImageJob.__table__, RrugcSourcePlanModel.__table__,
        OutboxEventModel.__table__, AgentDefinitionModel.__table__, AgentExperienceModel.__table__,
    ])
    db = Session(engine, expire_on_commit=False)
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def flag_config(**updates):
    values = dict(
        PROCESSING_JOBS_ENABLED=True,
        AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=False,
        AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED=False,
        AGENT_HARNESS_FEEDBACK_AUTO_PROCESS_ENABLED=False,
        AI_EMERGENCY_STOP_ENABLED=False,
    )
    values.update(updates)
    return SimpleNamespace(**values)


def seed(db: Session, *, tenant="boostgame", count=4, phrases=None):
    phrases = phrases or ("music lessons", "rock band", "jazz concert")
    outputs = []
    for n, phrase in enumerate(phrases):
        job = ImageJob(
            tenant_id=tenant, keyword_id=f"{tenant}-{n}-keyword",
            keyword_text=phrase, skill_source="codex",
            skill_name="creative-v3", prompt_text="unrelated prompt",
            created_by_user_id="human",
        )
        db.add(job)
        db.flush()
        for i in range(count):
            row = Output(
                tenant_id=tenant, stage="stage1", job_id=job.id,
                version=i + 1,
                output_name="design.png", content_type="image/png",
                remote_file_id=f"drive-{tenant}-{n}-{i}",
                font_info_json={
                    "content_sha256": sha256(f"{tenant}-{n}-{i}".encode()).hexdigest(),
                    "technical_qa_passed": True,
                    "concept_mode": "safe", "concept_variant": "A",
                    "creative_feedback": {
                        "decision": "picked",
                        "reason": "unspecified",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "updated_by": "reviewer",
                    },
                },
            )
            db.add(row)
            outputs.append((row, phrase))
    db.commit()
    return outputs


def setup_agent(db: Session, tenant="boostgame", version="v1"):
    AgentHarnessRepository(db).register_definition(
        tenant, AgentDefinition(
            agent_id="gatorhats.feedback-intelligence", version=version,
            capabilities=frozenset({"feedback_analyze"}),
            allowed_tools=frozenset({"feedback.read"}),
            preferred_executor="codex", max_concurrent=1,
        ), actor_id="operator",
    )
    db.commit()


def event_count(db, *, status=None):
    q = select(func.count()).select_from(OutboxEventModel).where(
        OutboxEventModel.event_type == EVENT_TYPE,
    )
    if status:
        q = q.where(OutboxEventModel.status == status)
    return db.scalar(q)


def change(db, row, phrase, *, decision="neutral", settings=None):
    before = dict((row.font_info_json or {}).get("creative_feedback") or {})
    current = set_design_feedback(
        row, decision=decision, reason="unspecified", actor_id="reviewer",
    )
    was_recorded = capture_if_enabled(
        db, row=row, keyword=phrase, previous_review=before,
        current_review=current, settings=settings or flag_config(),
    )
    db.commit()
    return was_recorded


def test_disabled_capture_has_zero_queue_side_effects(database):
    row, phrase = seed(database)[0]
    assert not change(database, row, phrase)
    assert event_count(database) == 0
    assert row.font_info_json["creative_feedback"]["decision"] == "neutral"


def test_single_changed_vote_is_logged_atomically_and_duplicate_is_noop(database):
    row, phrase = seed(database)[0]
    settings = flag_config(
        AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=True,
        AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED=True,
    )
    assert change(database, row, phrase, settings=settings)
    assert event_count(database) == 1
    event = database.scalar(select(OutboxEventModel))
    assert event.status == "pending"
    assert event.tenant_id == "boostgame"
    assert event.entity_id == row.id
    assert event.payload_json["cohort"] == "music"
    assert "music lessons" not in str(event.payload_json)
    assert "reviewer" not in str(event.payload_json)
    assert "unrelated prompt" not in str(event.payload_json)
    assert not change(database, row, phrase, settings=settings)
    assert event_count(database) == 1


def test_missing_cohort_or_emergency_stop_skips_capture(database):
    row, phrase = seed(database, phrases=("unclassified", "rock music", "jazz concert"))[0]
    on = flag_config(
        AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=True,
        AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED=True,
    )
    assert not change(database, row, phrase, settings=on)
    assert event_count(database) == 0
    row2, phrase2 = seed(database, tenant="other")[0]
    on.AI_EMERGENCY_STOP_ENABLED = True
    assert not change(database, row2, phrase2, settings=on)
    assert event_count(database) == 0


def test_capture_rollback_is_atomic(database):
    row, phrase = seed(database)[0]
    before = dict(row.font_info_json["creative_feedback"])
    updated = set_design_feedback(
        row, decision="rejected", reason="wrong_style", actor_id="reviewer",
    )
    assert record_stage1_feedback_change(
        database, row=row, keyword=phrase,
        previous_review=before, current_review=updated,
    )
    database.rollback()
    assert event_count(database) == 0
    database.refresh(row)
    assert row.font_info_json["creative_feedback"]["decision"] == "picked"


def test_draining_creates_candidate_and_revokes_stale_after_undo(database):
    outputs = seed(database)
    setup_agent(database)
    flags = flag_config(
        AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=True,
        AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED=True,
        AGENT_HARNESS_FEEDBACK_AUTO_PROCESS_ENABLED=True,
    )
    row, phrase = outputs[0]
    # Initial operator-approved aggregate 12 votes is already in candidate state.
    earlier = snapshots(database, tenant_id="boostgame", cohort="music", agent_version="v1")[0]
    old = AgentHarnessRepository(database).record_candidate(earlier.candidate)
    old.evaluation_json = {"source_kind": "stage1-human-feedback-evidence-v1", "promotion_allowed": False}
    database.commit()

    assert change(database, row, phrase, settings=flags)
    result = drain_feedback_events(database)
    database.commit()
    assert result.processed_events == 1
    assert result.candidates_revoked == 1
    assert result.candidates_created == 1
    assert event_count(database, status="published") == 1
    database.refresh(old)
    assert old.status == "rejected"
    assert old.evaluation_json["source_status"] == "stale_due_to_feedback_change"
    assert old.evaluation_json["promotion_allowed"] is False
    candidates = database.scalars(select(AgentExperienceModel).where(
        AgentExperienceModel.status == "candidate",
    )).all()
    assert len(candidates) == 1
    assert candidates[0].sample_count == 11
    assert candidates[0].confidence == 0.25
    assert candidates[0].verification_refs_json == []
    assert candidates[0].evaluation_json["capture_mode"] == "durable-outbox"

    assert drain_feedback_events(database).processed_events == 0
    assert database.scalar(select(func.count()).select_from(AgentExperienceModel)) == 2


def test_generic_outbox_is_never_claimed_or_published(database):
    outputs = seed(database)
    setup_agent(database)
    event = OutboxEventModel(
        tenant_id="boostgame", event_type="other.domain.action",
        entity_type="other", entity_id="x", idempotency_key="other:unique",
        payload_json={"anything": True},
    )
    database.add(event)
    database.commit()
    row, phrase = outputs[0]
    change(
        database, row, phrase,
        settings=flag_config(
            AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=True,
            AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED=True,
        ),
    )
    result = drain_feedback_events(database)
    database.commit()
    assert result.processed_events == 1
    database.refresh(event)
    assert event.status == "pending"


def test_missing_agent_marks_only_owned_events_failed_for_manual_backfill(database):
    row, phrase = seed(database)[0]
    change(
        database, row, phrase,
        settings=flag_config(
            AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=True,
            AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED=True,
        ),
    )
    result = drain_feedback_events(database)
    database.commit()
    assert result.unconfigured_cohorts == 1
    assert event_count(database, status="failed") == 1
    assert database.scalar(select(func.count()).select_from(AgentExperienceModel)) == 0


def test_other_tenant_feedback_never_updates_boostgame_candidate(database):
    seed(database)
    setup_agent(database)
    old_candidate = AgentHarnessRepository(database).record_candidate(
        snapshots(database, tenant_id="boostgame", cohort="music", agent_version="v1")[0].candidate
    )
    other = seed(database, tenant="other")
    setup_agent(database, tenant="other")
    row, phrase = other[0]
    change(
        database, row, phrase,
        settings=flag_config(
            AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=True,
            AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED=True,
        ),
    )
    result = drain_feedback_events(database)
    database.commit()
    database.refresh(old_candidate)
    assert old_candidate.status == "candidate"
    assert result.candidates_revoked == 0


def test_scheduler_guards_and_bounded_zero_cost_tick(database):
    disabled = flag_config()
    scheduler = AgentFeedbackOutboxScheduler(
        lambda: (_ for _ in ()).throw(RuntimeError("must not access DB")),
        disabled,
    )
    assert not scheduler.enabled
    assert scheduler.tick().processed_events == 0
    enabled = flag_config(
        AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=True,
        AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED=True,
        AGENT_HARNESS_FEEDBACK_AUTO_PROCESS_ENABLED=True,
    )
    scheduler = AgentFeedbackOutboxScheduler(lambda: database, enabled)
    assert scheduler.enabled
    assert scheduler.tick().processed_events == 0
    enabled.AI_EMERGENCY_STOP_ENABLED = True
    assert scheduler.tick().processed_events == 0


def test_http_stage1_feedback_and_stage2_toggle_emit_only_real_events(monkeypatch, database):
    from app.modules.realistic_review_ugc import router as rr
    row, phrase = seed(database)[0]
    principal = SimpleNamespace(
        user_id="reviewer", active_tenant_id="boostgame", platform_admin=False,
        effective_permissions=frozenset({"realistic_review_ugc.run"}),
    )
    settings = flag_config(
        AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=True,
        AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED=True,
    )
    monkeypatch.setattr(rr, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(rr.router)
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    app.dependency_overrides[get_db] = lambda: database
    with TestClient(app, raise_server_exceptions=False) as client:
        base = f"/api/v1/realistic-review-ugc/keyword-images/jobs/{row.job_id}/versions/{row.version}"
        r1 = client.post(base + "/design-feedback", json={"decision": "rejected", "reason": "wrong_style"})
        assert r1.status_code == 200, r1.text
        assert event_count(database) == 1
        repeat = client.post(base + "/design-feedback", json={"decision": "rejected", "reason": "wrong_style"})
        assert repeat.status_code == 200
        assert event_count(database) == 1
        undo = client.post(base + "/products-stage2?selected=false")
        assert undo.status_code == 200, undo.text
        assert event_count(database) == 2
        assert database.get(Output, row.id).font_info_json["creative_feedback"]["decision"] == "neutral"


def test_admin_outbox_status_reports_only_tenant_counts(monkeypatch, database):
    from app.modules.agent_harness import router as agent_routes
    row, phrase = seed(database)[0]
    settings = flag_config(
        AGENT_HARNESS_READ_API_ENABLED=False,
        AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=True,
        AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED=True,
    )
    change(database, row, phrase, settings=settings)
    principal = SimpleNamespace(
        user_id="operator", active_tenant_id="boostgame", platform_admin=False,
        effective_permissions=frozenset({"ai_operations.read", "ai_provider.configure"}),
    )
    monkeypatch.setattr(agent_routes, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(agent_routes.router)
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    app.dependency_overrides[get_db] = lambda: database
    with TestClient(app, raise_server_exceptions=False) as client:
        endpoint = "/api/v1/admin/agent-harness/boostgame/feedback/stage1/outbox-status"
        assert client.get(endpoint).status_code == 404
        settings.AGENT_HARNESS_READ_API_ENABLED = True
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["counts"] == {
            "pending": 1, "processing": 0, "published": 0, "failed": 0,
        }
        assert result["auto_capture_enabled"] is True
        assert result["auto_process_enabled"] is False
        assert result["execution_enabled"] is False
        assert result["automatic_promotion_enabled"] is False
        assert "drive" not in str(result).lower()
        assert "music lessons" not in str(result)
        principal.active_tenant_id = "different"
        assert client.get(endpoint).status_code == 403
        principal.active_tenant_id = "boostgame"
        principal.effective_permissions = frozenset({"ai_operations.read"})
        assert client.get(endpoint).status_code == 403
