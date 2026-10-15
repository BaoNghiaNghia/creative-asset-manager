"""Cap Stack lane is independent of campaign grouping and agent leases."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.realistic_review_ugc.cap_stack_model import (
    RrugcCapStackControlModel, RrugcCapStackImageModel,
)
from app.modules.realistic_review_ugc import cap_stack_router as r


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcCapStackControlModel.__table__.create(engine)
    RrugcCapStackImageModel.__table__.create(engine)
    ProcessingJobModel.__table__.create(engine)
    TenantProcessingPolicyModel.__table__.create(engine)
    monkeypatch.setattr(r, "agent_auth", lambda _session, _agent_id, _header: SimpleNamespace(tenant_id="tenant-a"))
    with Session(engine) as db:
        yield db
    engine.dispose()


def user(tenant="tenant-a"):
    return SimpleNamespace(active_tenant_id=tenant, user_id="qa")


def candidate(number=123, source="Cap Stack"):
    return r.ScoutBatch(items=[r.ScoutCandidate(
        pin_url=f"https://www.pinterest.com/pin/{number}/",
        image_url=f"https://i.pinimg.com/736x/{number}.jpg",
        alt_text="Stacked baseball caps on table",
    )], source_query=source)


def claim(session):
    return r.claim_cap_stack("agent-one", "Bearer test", session)


def test_cap_stack_disabled_by_default_and_lease_isolated(session):
    assert claim(session) is None
    r.set_cap_stack_settings(r.SettingsRequest(enabled=True), session, user())
    first = claim(session)
    assert first["query"] == "Cap Stack"
    assert first["scout_type"] == "cap_stack"
    assert first["campaign_id"] == "cap-stack-shared"
    assert first["target_count"] == 12
    assert claim(session) is None
    with pytest.raises(HTTPException) as error:
        r.submit_cap_stack("agent-one", str(uuid4()), candidate(), "Bearer test", session)
    assert error.value.status_code == 409
    r.heartbeat_cap_stack("agent-one", first["run"]["id"], r.HeartbeatRequest(), "Bearer test", session)
    r.set_cap_stack_settings(r.SettingsRequest(enabled=False), session, user())
    assert claim(session) is None


def test_library_deduplicates_by_tenant_and_trains_on_human_feedback(session):
    r.set_cap_stack_settings(r.SettingsRequest(enabled=True), session, user())
    run = claim(session)["run"]["id"]
    result = r.submit_cap_stack("agent-one", run, candidate(), "Bearer test", session)
    assert result["created"] == 1
    duplicate = r.submit_cap_stack("agent-one", run, candidate(), "Bearer test", session)
    assert duplicate["existing"] == 1 and duplicate["created"] == 0
    page = r.list_cap_stack(q="", label="all", caps_count=None, page=1, page_size=20,
                            session=session, principal=user())
    assert page["stats"]["pending"] == 1
    assert page["items"][0]["caps_detected"] is None
    image_id = page["items"][0]["id"]
    checked = r.set_cap_stack_feedback(image_id, r.FeedbackRequest(label="good", caps_count=4),
                                       session, user())
    assert checked["manual_label"] == "good"
    assert checked["manual_caps_count"] == 4
    assert r.list_cap_stack(q="", label="good", caps_count=4, page=1, page_size=20,
                            session=session, principal=user())["total"] == 1
    assert r.list_cap_stack(q="", label="bad", caps_count=None, page=1, page_size=20,
                            session=session, principal=user())["total"] == 0
    assert r.cap_stack_learning_queries(session, "tenant-a")[0] == "Cap Stack"
    cleared = r.set_cap_stack_feedback(image_id, r.FeedbackRequest(label="clear"), session, user())
    assert cleared["manual_label"] is None
    r.complete_cap_stack("agent-one", run, r.CompleteRequest(status="completed"), "Bearer test", session)
    assert claim(session) is None




def test_retry_gemini_after_low_confidence_keeps_manual_feedback(session):
    r.set_cap_stack_settings(r.SettingsRequest(enabled=True), session, user())
    run = claim(session)["run"]["id"]
    r.submit_cap_stack("agent-one", run, candidate(), "Bearer test", session)
    from app.modules.processing.model import ProcessingJobModel
    first = list(session.query(ProcessingJobModel).all())
    assert len(first) == 1
    image = session.query(RrugcCapStackImageModel).first()
    image.count_status = "needs_review"
    image.caps_detected = 3
    image.manual_caps_count = 4
    image.manual_label = "good"
    session.commit()
    response = r.retry_cap_stack_vision(image.id, session, user())
    assert response["image"]["count_status"] == "pending"
    assert response["image"]["manual_caps_count"] == 4
    assert response["image"]["manual_label"] == "good"
    assert response["vision_queued"] == 1
    assert len(list(session.query(ProcessingJobModel).all())) == 2
    with pytest.raises(HTTPException) as error:
        r.retry_cap_stack_vision(image.id, session, user())
    assert error.value.status_code == 409


def test_gemini_analysis_fields_are_tenant_isolated(session):
    r.set_cap_stack_settings(r.SettingsRequest(enabled=True), session, user())
    run = claim(session)["run"]["id"]
    r.submit_cap_stack("agent-one", run, candidate(), "Bearer test", session)
    image = session.query(RrugcCapStackImageModel).first()
    image.count_status = "ready"
    image.is_cap_stack = True
    image.caps_detected = 4
    image.count_confidence = 0.99
    image.ai_suspected = True
    image.visual_summary = "A pile of four trucker caps with overlapping brims."
    session.commit()
    view = r.list_cap_stack(q="", label="all", caps_count=None, vision_status="ready", page=1, page_size=20,
                            session=session, principal=user())
    assert view["vision"]["ready"] == 1
    assert view["vision"]["ai_suspected"] == 1
    assert view["items"][0]["caps_detected"] == 4
    assert view["items"][0]["ai_suspected"] is True
    other = r.list_cap_stack(q="", label="all", caps_count=None, vision_status="ready", page=1, page_size=20,
                             session=session, principal=user("tenant-b"))
    assert other["total"] == 0
    assert other["vision"]["ready"] == 0

def test_urls_fail_closed_and_wrong_tenant_cannot_see_library(session):
    for url in ("http://www.pinterest.com/pin/123/", "https://evil.test/pin/123/",
                "https://www.pinterest.com/not-a-pin/123/"):
        with pytest.raises(HTTPException):
            r.pin_key(url)
    for url in ("http://i.pinimg.com/image.jpg", "https://evil.test/image.jpg",
                "https://example.pinimg.com.evil.test/image.jpg"):
        with pytest.raises(HTTPException):
            r.validate_image_url(url)
    r.set_cap_stack_settings(r.SettingsRequest(enabled=True), session, user())
    run = claim(session)["run"]["id"]
    r.submit_cap_stack("agent-one", run, candidate(), "Bearer test", session)
    assert r.list_cap_stack(q="", label="all", caps_count=None, page=1, page_size=20,
                            session=session, principal=user("tenant-b"))["total"] == 0


def test_feedback_rejects_cross_tenant_and_count_outside_2_to_6(session):
    r.set_cap_stack_settings(r.SettingsRequest(enabled=True), session, user())
    run = claim(session)["run"]["id"]
    r.submit_cap_stack("agent-one", run, candidate(), "Bearer test", session)
    entry = r.list_cap_stack(q="", label="all", caps_count=None, page=1, page_size=20,
                            session=session, principal=user())["items"][0]
    with pytest.raises(HTTPException) as error:
        r.set_cap_stack_feedback(entry["id"], r.FeedbackRequest(label="good"),
                                 session, user("tenant-b"))
    assert error.value.status_code == 404
    for invalid in (1, 7, 51):
        with pytest.raises(Exception):
            r.FeedbackRequest(caps_count=invalid)
    with pytest.raises(HTTPException) as error:
        r.set_cap_stack_feedback(entry["id"], r.FeedbackRequest(label="good"), session, user())
    assert error.value.status_code == 422
    image = session.get(RrugcCapStackImageModel, entry["id"])
    image.count_status = "out_of_range"
    image.caps_detected = 7
    session.commit()
    with pytest.raises(HTTPException) as error:
        r.set_cap_stack_feedback(entry["id"], r.FeedbackRequest(label="good"), session, user())
    assert error.value.status_code == 422
    approved = r.set_cap_stack_feedback(entry["id"], r.FeedbackRequest(label="good", caps_count=6), session, user())
    assert approved["manual_label"] == "good"
    assert approved["manual_caps_count"] == 6