"""G3 durable queue lifecycle, tenant isolation, lease fencing and DNA persistence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.realistic_review_ugc.model import RrugcReferenceAssetModel as Asset
from app.modules.realistic_review_ugc.typography_analysis_queue import TypographyAnalysisQueue
from app.modules.realistic_review_ugc.typography_model import (
    RrugcTypographyReferenceModel as Ref,
    RrugcTypographyStyleDnaModel as Dna,
    RrugcTypographyAnalysisJobModel as Job,
)

CLOCK = datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    for model in (Asset, Ref, Dna, Job):
        model.__table__.create(engine)
    yield engine
    engine.dispose()


def add_ref(session: Session, *, tenant="tenant-a", name="a", owner="owner",
            rights="owned", privacy="private"):
    sha = name[0] * 64
    asset = Asset(
        id="asset-" + tenant + name,
        tenant_id=tenant,
        source_type="upload",
        source_key=sha,
        content_hash=sha,
        profile_key="typography-idea",
        reference_type="other",
        status="ready",
        remote_file_id="fake-" + name,
        created_by_user_id=owner,
    )
    session.add(asset)
    session.flush()
    ref = Ref(
        id="ref-" + tenant + name,
        tenant_id=tenant,
        workspace_id=tenant,
        asset_id=asset.id,
        content_hash=sha,
        source_rights_status=rights,
        privacy=privacy,
        created_by_user_id=owner,
    )
    session.add(ref)
    session.flush()
    return ref


def enqueue(queue, ref, *, user="owner", **kwargs):
    return queue.enqueue(
        tenant_id=ref.tenant_id, reference_id=ref.id, user_id=user,
        analyzer_version=kwargs.pop("analyzer_version", "analyzer-1"),
        enabled=kwargs.pop("enabled", True),
        tenant_allowed=kwargs.pop("tenant_allowed", True),
        now=kwargs.pop("now", CLOCK), **kwargs,
    )


def claim(queue, *, tenant="tenant-a", owner="worker-1", now=CLOCK, concurrency=1, **kwargs):
    return queue.claim(tenant_id=tenant, owner=owner, now=now, concurrency=concurrency,
                       enabled=kwargs.pop("enabled", True), tenant_allowed=kwargs.pop("tenant_allowed", True), **kwargs)


def dna_payload(sha):
    return {
        "schema_version": "typography-dna/v1", "analyzer_version": "analyzer-1",
        "image_hash": sha, "typography_region": {"x": .1, "y": .2, "w": .7, "h": .5},
        "style_family": "arched-serif", "font_mood": {
            "primary": "serif", "secondary": None, "weight": "heavy",
            "contrast": "medium", "proportions": "wide",
        },
        "layout_graph": {"nodes": [
            {"id": "main", "role": "primary", "size_ratio": 1.0,
             "path_type": "arch", "align": "center"}
        ], "edges": []},
        "overlap_rules": [], "confidence": .82,
    }


def test_enqueue_is_durable_idempotent_and_fail_closed(engine):
    with Session(engine) as db:
        ref = add_ref(db)
        q = TypographyAnalysisQueue(db)
        assert enqueue(q, ref, enabled=False) == (None, "feature_disabled")
        assert enqueue(q, ref, tenant_allowed=False) == (None, "feature_disabled")
        assert enqueue(q, ref, user="outsider") == (None, "reference_unavailable")
        assert enqueue(q, ref)[1] == "created"
        assert enqueue(q, ref)[1] == "existing"
        assert claim(q, enabled=False) is None
        assert claim(q, tenant_allowed=False) is None
        db.commit()
    with Session(engine) as db:
        assert db.scalar(select(Job)).status == "queued"


def test_rights_and_cross_tenant_gate_even_if_job_is_queued(engine):
    with Session(engine) as db:
        ref = add_ref(db, rights="unknown")
        other = add_ref(db, tenant="tenant-b", name="b", rights="licensed")
        q = TypographyAnalysisQueue(db)
        assert enqueue(q, ref)[1] == "rights_unverified"
        assert enqueue(q, other)[1] == "created"
        assert claim(q, tenant="tenant-a") is None
        assert claim(q, tenant="tenant-b") is not None
        ref.source_rights_status = "licensed"
        queued, reason = enqueue(q, ref)
        assert reason == "created"
        ref.source_rights_status = "blocked"
        assert claim(q, tenant="tenant-a") is None


def test_single_lease_token_heartbeat_capacity_and_retries(engine):
    with Session(engine) as db:
        a = add_ref(db, name="a")
        b = add_ref(db, name="b")
        q = TypographyAnalysisQueue(db)
        enqueue(q, a)
        enqueue(q, b)
        first = claim(q)
        token = first.lease_token
        assert first.status == "running" and first.attempt_count == 1
        assert claim(q) is None
        assert claim(q, concurrency=2, owner="worker-2") is not None
        assert not q.heartbeat(enabled=True, tenant_allowed=True, tenant_id="tenant-b", job_id=first.id, token=token, now=CLOCK)
        assert not q.heartbeat(enabled=True, tenant_allowed=True, tenant_id="tenant-a", job_id=first.id, token="stale", now=CLOCK)
        assert q.heartbeat(enabled=True, tenant_allowed=True, tenant_id="tenant-a", job_id=first.id, token=token, now=CLOCK)
        assert q.fail(tenant_id="tenant-a", job_id=first.id,
                      token="stale", code="timeout", now=CLOCK) == "stale_lease"
        assert q.fail(tenant_id="tenant-a", job_id=first.id,
                      token=token, code="timeout", now=CLOCK) == "queued"
        assert first.next_attempt_at is not None
        assert claim(q, now=CLOCK + timedelta(seconds=20), concurrency=2) is None
        second_attempt = claim(q, now=CLOCK + timedelta(seconds=31), concurrency=2)
        assert second_attempt is first and first.attempt_count == 2
        assert first.lease_token != token
        assert q.fail(tenant_id="tenant-a", job_id=first.id,
                      token=first.lease_token, code="timeout",
                      now=CLOCK + timedelta(seconds=31)) == "queued"
        assert claim(q, now=CLOCK + timedelta(seconds=149), concurrency=2) is None
        third = claim(q, now=CLOCK + timedelta(seconds=152), concurrency=2)
        assert third is first and third.attempt_count == 3
        assert q.fail(tenant_id="tenant-a", job_id=first.id,
                      token=third.lease_token, code="timeout",
                      now=CLOCK + timedelta(seconds=152)) == "failed"
        assert first.next_attempt_at is None


def test_expired_lease_recovered_without_reusing_stale_token(engine):
    with Session(engine) as db:
        ref = add_ref(db)
        q = TypographyAnalysisQueue(db)
        enqueue(q, ref)
        first = claim(q)
        old = first.lease_token
        assert claim(q, now=CLOCK + timedelta(seconds=121)) is None
        assert first.status == "queued"
        assert first.last_error_code == "lease_expired"
        assert not q.heartbeat(enabled=True, tenant_allowed=True, tenant_id=ref.tenant_id, job_id=first.id, token=old,
                               now=CLOCK + timedelta(seconds=121))
        recovered = claim(q, now=CLOCK + timedelta(seconds=152))
        assert recovered.id == first.id and recovered.lease_token != old
        assert recovered.attempt_count == 2


def test_dna_complete_atomic_version_hash_and_replay_guard(engine):
    with Session(engine) as db:
        ref = add_ref(db)
        q = TypographyAnalysisQueue(db)
        enqueue(q, ref)
        job = claim(q)
        token = job.lease_token
        assert q.complete(enabled=False, tenant_allowed=True,
                          tenant_id=ref.tenant_id, job_id=job.id,
                          token=token, payload=dna_payload(ref.content_hash),
                          now=CLOCK) == "feature_disabled"
        assert q.complete(enabled=True, tenant_allowed=False,
                          tenant_id=ref.tenant_id, job_id=job.id,
                          token=token, payload=dna_payload(ref.content_hash),
                          now=CLOCK) == "feature_disabled"
        assert not q.heartbeat(enabled=False, tenant_allowed=True,
                               tenant_id=ref.tenant_id, job_id=job.id,
                               token=token, now=CLOCK)
        bad = dna_payload(ref.content_hash)
        bad["image_hash"] = "f" * 64
        assert q.complete(enabled=True, tenant_allowed=True, tenant_id=ref.tenant_id, job_id=job.id,
                          token=token, payload=bad, now=CLOCK) == "needs_review"
        assert db.query(Dna).count() == 0
        assert q.complete(enabled=True, tenant_allowed=True, tenant_id=ref.tenant_id, job_id=job.id,
                          token=token, payload=dna_payload(ref.content_hash),
                          now=CLOCK) == "stale_lease"


def test_valid_dna_complete_and_no_repeat_job(engine):
    with Session(engine) as db:
        ref = add_ref(db)
        q = TypographyAnalysisQueue(db)
        enqueue(q, ref)
        job = claim(q)
        token = job.lease_token
        assert q.complete(enabled=True, tenant_allowed=True, tenant_id=ref.tenant_id, job_id=job.id, token=token,
                          payload=dna_payload(ref.content_hash),
                          perceptual_hash="tdh1:0123456789abcdef",
                          now=CLOCK) == "completed"
        ref_id, job_id = ref.id, job.id
        db.commit()
    with Session(engine) as db:
        ref = db.get(Ref, ref_id)
        job = db.get(Job, job_id)
        q = TypographyAnalysisQueue(db)
        assert job.status == "completed"
        assert ref.analysis_status == "ready"
        assert db.query(Dna).count() == 1
        assert db.query(Dna).one().perceptual_hash == "tdh1:0123456789abcdef"
        assert enqueue(q, ref)[1] == "cache_hit"
        assert claim(q) is None


def test_invalid_server_perceptual_hash_is_never_persisted(engine):
    with Session(engine) as db:
        ref = add_ref(db)
        q = TypographyAnalysisQueue(db)
        enqueue(q, ref)
        job = claim(q)
        assert q.complete(
            enabled=True, tenant_allowed=True, tenant_id=ref.tenant_id,
            job_id=job.id, token=job.lease_token, now=CLOCK,
            payload=dna_payload(ref.content_hash),
            perceptual_hash="untrusted-provider-output",
        ) == "needs_review"
        assert db.query(Dna).count() == 0


def test_rights_revoked_during_lease_cannot_store_dna(engine):
    with Session(engine) as db:
        ref = add_ref(db)
        q = TypographyAnalysisQueue(db)
        enqueue(q, ref)
        job = claim(q)
        ref.source_rights_status = "blocked"
        db.flush()
        assert not q.heartbeat(enabled=True, tenant_allowed=True,
                               tenant_id=ref.tenant_id, job_id=job.id,
                               token=job.lease_token, now=CLOCK)
        assert q.complete(enabled=True, tenant_allowed=True, tenant_id=ref.tenant_id, job_id=job.id,
                          token=job.lease_token,
                          payload=dna_payload(ref.content_hash),
                          now=CLOCK) == "needs_review"
        assert db.query(Dna).count() == 0


def test_no_manual_override_of_bad_capacity_or_version(engine):
    with Session(engine) as db:
        ref = add_ref(db)
        q = TypographyAnalysisQueue(db)
        with pytest.raises(ValueError):
            enqueue(q, ref, analyzer_version="../not-allowed")
        with pytest.raises(ValueError):
            claim(q, concurrency=3)
        with pytest.raises(ValueError):
            claim(q, owner="")
