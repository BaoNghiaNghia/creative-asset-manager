"""Disposable PostgreSQL 12/16 checks for the inert fenced PNG receipt ledger."""
from __future__ import annotations

import hashlib
import io
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from app.modules.agent_harness.executor_slots import reserve_existing_image_job
from app.modules.agent_harness.fenced_output import (
    accept_fenced_image_receipt, prepare_fenced_image_receipt,
)
from app.modules.agent_harness.model import (
    AgentExecutorSlotModel, AgentImageOutputReceiptModel,
)
from app.modules.etsy_spy.model import EtsySpyAgent
from app.modules.processing.model import ProcessingJobModel


@pytest.fixture
def ledger():
    url = os.environ.get("INTEGRATION_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.skip("requires explicitly provisioned disposable PostgreSQL integration DB")
    engine = create_engine(url, pool_pre_ping=True, pool_size=4, max_overflow=4)
    tenant = "receipt-" + uuid4().hex
    agent = str(uuid4())
    job = str(uuid4())
    clock = datetime.now(timezone.utc).replace(microsecond=654321)
    with Session(engine) as session:
        session.add(EtsySpyAgent(
            id=agent, tenant_id=tenant, name="CAM Image Agent / Receipt CI",
            token_hash="c" * 64, active=True,
        ))
        session.add(ProcessingJobModel(
            id=job, tenant_id=tenant, job_type="etsy_design_extract",
            entity_type="etsy_spy_listing", entity_id=uuid4().hex,
            idempotency_key="output-receipt-" + uuid4().hex,
            payload_json={"execution_mode": "orchestrator_v1"},
            status="processing", attempt_count=1, max_attempts=5,
            claimed_by="etsy-image:" + agent, claimed_at=clock,
            lease_expires_at=clock + timedelta(minutes=2),
        ))
        session.commit()
    yield engine, tenant, agent, job, clock
    with Session(engine) as session:
        session.execute(delete(AgentImageOutputReceiptModel).where(
            AgentImageOutputReceiptModel.tenant_id == tenant,
        ))
        session.execute(delete(AgentExecutorSlotModel).where(
            AgentExecutorSlotModel.tenant_id == tenant,
        ))
        session.execute(delete(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant,
        ))
        session.execute(delete(EtsySpyAgent).where(EtsySpyAgent.tenant_id == tenant))
        session.commit()
    engine.dispose()


def _png(color="blue"):
    image = Image.new("RGBA", (3, 4), color)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _reserve(session, tenant, agent, job, now):
    return reserve_existing_image_job(
        session, tenant_id=tenant, agent_id=agent, job_id=job,
        enabled=True, emergency_stop=False, now=now,
    )


def _prepare(session, tenant, agent, job, now, data, **overrides):
    params = dict(
        tenant_id=tenant, agent_id=agent, job_id=job, fencing_token=1,
        output_sha256=hashlib.sha256(data).hexdigest(),
        output_bytes=len(data), source_sha256="a" * 64,
        engine="chatgpt_assisted",
        enabled=True, emergency_stop=False, now=now,
    )
    params.update(overrides)
    return prepare_fenced_image_receipt(session, **params)


def _accept(session, tenant, agent, job, now, data, root, **overrides):
    params = dict(
        tenant_id=tenant, agent_id=agent, job_id=job, fencing_token=1,
        output_sha256=hashlib.sha256(data).hexdigest(), raw_png=data,
        storage_root=root, source_sha256="a" * 64,
        engine="chatgpt_assisted",
        enabled=True, emergency_stop=False, provenance_verified=True, now=now,
    )
    params.update(overrides)
    return accept_fenced_image_receipt(session, **params)


def test_read_only_reconciliation_reports_prepared_accepted_and_missing_bytes(
    ledger, tmp_path,
):
    from app.modules.agent_harness.fenced_reconciliation import inspect_fenced_receipt

    engine, tenant, agent, job, clock = ledger
    png = _png()
    with Session(engine) as session:
        assert inspect_fenced_receipt(
            session, tenant_id=tenant, job_id=job, storage_root=tmp_path,
        ).state == "feature_disabled"
        assert inspect_fenced_receipt(
            session, tenant_id=tenant, job_id=job, storage_root=tmp_path,
            enabled=True, emergency_stop=True,
        ).state == "emergency_stop"
        assert inspect_fenced_receipt(
            session, tenant_id="not-" + tenant, job_id=job, storage_root=tmp_path,
            enabled=True, emergency_stop=False,
        ).state == "job_not_found"
        assert _reserve(session, tenant, agent, job, clock).state == "reserved"
        session.commit()
    with Session(engine) as session:
        assert _prepare(session, tenant, agent, job, clock + timedelta(seconds=1), png).state == "prepared"
        session.commit()
    with Session(engine) as session:
        pending = inspect_fenced_receipt(
            session, tenant_id=tenant, job_id=job, storage_root=tmp_path,
            enabled=True, emergency_stop=False,
            now=clock + timedelta(seconds=2),
        )
        assert pending.state == "prepared_awaiting_blob"
        assert not pending.manual_reconcile_required
        session.rollback()
    with Session(engine) as session:
        outcome = _accept(
            session, tenant, agent, job, clock + timedelta(seconds=3), png, tmp_path,
        )
        assert outcome.state == "accepted"
        session.commit()
    with Session(engine) as session:
        accepted = inspect_fenced_receipt(
            session, tenant_id=tenant, job_id=job, storage_root=tmp_path,
            enabled=True, emergency_stop=False,
            now=clock + timedelta(seconds=4),
        )
        assert accepted.state == "accepted_verified"
        assert not accepted.manual_reconcile_required
        session.rollback()
    (tmp_path / accepted.storage_ref).unlink()
    with Session(engine) as session:
        damaged = inspect_fenced_receipt(
            session, tenant_id=tenant, job_id=job, storage_root=tmp_path,
            enabled=True, emergency_stop=False,
            now=clock + timedelta(seconds=4),
        )
        assert damaged.state == "accepted_blob_missing"
        assert damaged.manual_reconcile_required
        # The read-only audit must not reset the completed job/receipt.
        assert session.get(ProcessingJobModel, job).status == "completed"
        assert session.get(AgentImageOutputReceiptModel, (tenant, job)).status == "accepted"
        session.rollback()



def _install_4c3g_job_source(engine, tenant, agent, job, clock, url):
    with Session(engine) as session:
        source = session.get(ProcessingJobModel, job)
        source.payload_json = {
            **source.payload_json,
            "source": {"type": "etsy_cdn", "url": url},
            "source_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
        }
        assert _reserve(session, tenant, agent, job, clock).state == "reserved"
        session.commit()


def _jpg_for_capture():
    image = Image.new("RGB", (4, 5), "blue")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")
    return buffer.getvalue()


def test_4c3g_source_capture_has_no_database_lock_during_HTTPS(ledger, tmp_path, monkeypatch):
    from app.modules.agent_harness import fenced_source_staging as staging
    from app.modules.agent_harness.fenced_source_provenance import verify_server_source_capture
    from app.modules.agent_harness.fenced_storage import read_fenced_blob_pinned

    engine, tenant, agent, job, clock = ledger
    url = "https://i.etsystatic.com/1234567/r/il/allowed-source.jpg"
    jpeg = _jpg_for_capture()
    _install_4c3g_job_source(engine, tenant, agent, job, clock, url)
    calls = []

    def network_without_pg_lock(source_url):
        calls.append(source_url)
        # If the staging orchestrator still holds the job or device lock, a
        # second connection's NOWAIT row lock MUST fail on real PostgreSQL.
        with Session(engine) as independent:
            locked = independent.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.id == job,
                ProcessingJobModel.tenant_id == tenant,
            ).with_for_update(nowait=True))
            assert locked.id == job
            independent.rollback()
        return jpeg

    monkeypatch.setattr(staging, "_fetch_etsy_source", network_without_pg_lock)
    opts = dict(
        session_factory=lambda: Session(engine),
        tenant_id=tenant, agent_id=agent, job_id=job,
        fencing_token=1, signing_key=bytes(range(32)),
        storage_root=tmp_path, now=clock + timedelta(seconds=1),
    )
    assert staging.capture_server_source_in_stages(**opts).state == "feature_disabled"
    assert staging.capture_server_source_in_stages(
        **opts, enabled=True,
    ).state == "emergency_stop"
    assert calls == []

    accepted = staging.capture_server_source_in_stages(
        **opts, enabled=True, emergency_stop=False,
    )
    assert accepted.state == "captured"
    assert accepted.source_sha256 == hashlib.sha256(jpeg).hexdigest()
    assert accepted.source_bytes == len(jpeg)
    assert calls == [url]
    with Session(engine) as session:
        source = session.get(ProcessingJobModel, job)
        verified = verify_server_source_capture(
            source, agent_id=agent,
            reported_source_sha256=accepted.source_sha256,
            signing_key=opts["signing_key"], storage_root=tmp_path,
            enabled=True, emergency_stop=False, now=clock + timedelta(seconds=2),
        )
        assert verified.state == "source_capture_verified"
        assert not verified.may_accept_output
        assert not verified.browser_provenance_verified
        assert not verified.provider_provenance_verified
        raw = read_fenced_blob_pinned(
            tmp_path, namespace="fenced-source-v1",
            key=accepted.source_sha256, expected_sha256=accepted.source_sha256,
            expected_bytes=len(jpeg),
        )
        assert raw == jpeg
        session.rollback()

    same = staging.capture_server_source_in_stages(
        **opts, enabled=True, emergency_stop=False,
    )
    assert same.state == "already_captured"
    assert calls == [url]  # no unnecessary second network request

    # An old manifest never silently signs/replaces a new claim epoch.
    with Session(engine) as session:
        source = session.get(ProcessingJobModel, job)
        source.attempt_count = 2
        source.claimed_at = clock + timedelta(seconds=41)
        source.lease_expires_at = clock + timedelta(minutes=3)
        session.commit()
    with Session(engine) as session:
        replacement = _reserve(
            session, tenant, agent, job, clock + timedelta(seconds=42),
        )
        assert replacement.state == "reserved"
        assert replacement.fencing_token == 2
        session.commit()
    old_capture = staging.capture_server_source_in_stages(
        **{**opts, "fencing_token": 2, "now": clock + timedelta(seconds=43)},
        enabled=True, emergency_stop=False,
    )
    assert old_capture.state == "source_capture_requires_reconcile"
    assert calls == [url]


def test_4c3g_crash_after_fsync_before_manifest_commit_recovers_without_overwrite(
    ledger, tmp_path, monkeypatch,
):
    from app.modules.agent_harness import fenced_source_staging as staging
    from app.modules.agent_harness.fenced_storage import read_fenced_blob_pinned

    engine, tenant, agent, job, clock = ledger
    url = "https://img.etsystatic.com/328010/r/il/test.jpg"
    jpeg = _jpg_for_capture()
    _install_4c3g_job_source(engine, tenant, agent, job, clock, url)
    calls = []
    monkeypatch.setattr(staging, "_fetch_etsy_source", lambda u: (
        calls.append(u) or jpeg
    ))
    opts = dict(
        session_factory=lambda: Session(engine),
        tenant_id=tenant, agent_id=agent, job_id=job,
        fencing_token=1, signing_key=b"x" * 32,
        storage_root=tmp_path, enabled=True, emergency_stop=False,
        now=clock + timedelta(seconds=1),
    )
    real_finalize = staging._finalize_under_lock

    def interrupt_after_fsync(session, **kwargs):
        # Both source bytes and namespace entries are already on disk;
        # simulate process death before SQL can commit the signed manifest.
        digest = hashlib.sha256(jpeg).hexdigest()
        assert read_fenced_blob_pinned(
            tmp_path, namespace="fenced-source-v1",
            key=digest, expected_sha256=digest,
            expected_bytes=len(jpeg),
        ) == jpeg
        raise RuntimeError("simulated_process_crash_after_fsync")

    monkeypatch.setattr(staging, "_finalize_under_lock", interrupt_after_fsync)
    with pytest.raises(RuntimeError, match="simulated_process_crash"):
        staging.capture_server_source_in_stages(**opts)
    with Session(engine) as session:
        stored = session.get(ProcessingJobModel, job)
        assert "_cam_fenced_source_capture_v1" not in stored.payload_json
        session.rollback()
    monkeypatch.setattr(staging, "_finalize_under_lock", real_finalize)
    second = staging.capture_server_source_in_stages(**opts)
    assert second.state == "captured"
    assert second.source_sha256 == hashlib.sha256(jpeg).hexdigest()
    assert calls == [url, url]  # fetch again, reuse same immutable content


def test_4c3g_cancel_or_change_source_during_fetch_cannot_sign_manifest(
    ledger, tmp_path, monkeypatch,
):
    from app.modules.agent_harness import fenced_source_staging as staging

    engine, tenant, agent, job, clock = ledger
    url = "https://i.etsystatic.com/1234567/r/il/old-source.jpg"
    jpeg = _jpg_for_capture()
    _install_4c3g_job_source(engine, tenant, agent, job, clock, url)
    opts = dict(
        session_factory=lambda: Session(engine),
        tenant_id=tenant, agent_id=agent, job_id=job,
        fencing_token=1, signing_key=b"x" * 32,
        storage_root=tmp_path, enabled=True, emergency_stop=False,
        now=clock + timedelta(seconds=2),
    )

    def cancel_during_fetch(source_url):
        # This update must never wait for source HTTP to release a PG lock.
        with Session(engine) as session:
            source = session.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.id == job,
            ).with_for_update(nowait=True))
            source.cancellation_requested = True
            session.commit()
        return jpeg

    monkeypatch.setattr(staging, "_fetch_etsy_source", cancel_during_fetch)
    denied = staging.capture_server_source_in_stages(**opts)
    assert denied.state == "job_claim_not_verified"
    with Session(engine) as session:
        source = session.get(ProcessingJobModel, job)
        assert "_cam_fenced_source_capture_v1" not in source.payload_json
        source.cancellation_requested = False
        session.commit()

    def replace_url_during_fetch(source_url):
        with Session(engine) as session:
            source = session.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.id == job,
            ).with_for_update(nowait=True))
            different = "https://i.etsystatic.com/9999/r/il/revised.jpg"
            source.payload_json = {
                **source.payload_json,
                "source": {"type": "etsy_cdn", "url": different},
                "source_url_sha256": hashlib.sha256(different.encode()).hexdigest(),
            }
            session.commit()
        return jpeg

    monkeypatch.setattr(staging, "_fetch_etsy_source", replace_url_during_fetch)
    stale = staging.capture_server_source_in_stages(**opts)
    assert stale.state == "capture_plan_stale"
    with Session(engine) as session:
        source = session.get(ProcessingJobModel, job)
        assert "_cam_fenced_source_capture_v1" not in source.payload_json
        session.rollback()


def test_4c3g_wrong_epoch_during_fetch_never_promotes_old_claim(ledger, tmp_path, monkeypatch):
    from app.modules.agent_harness import fenced_source_staging as staging

    engine, tenant, agent, job, clock = ledger
    url = "https://i.etsystatic.com/1234567/r/il/old.jpg"
    _install_4c3g_job_source(engine, tenant, agent, job, clock, url)
    jpeg = _jpg_for_capture()
    opts = dict(
        session_factory=lambda: Session(engine),
        tenant_id=tenant, agent_id=agent, job_id=job,
        fencing_token=1, signing_key=b"x" * 32,
        storage_root=tmp_path, enabled=True, emergency_stop=False,
        now=clock + timedelta(seconds=42),
    )
    # Simulate reclaim after an earlier 0209 slot has expired.
    with Session(engine) as session:
        src = session.get(ProcessingJobModel, job)
        src.attempt_count = 2
        src.claimed_at = clock + timedelta(seconds=40)
        src.lease_expires_at = clock + timedelta(minutes=3)
        session.commit()
    with Session(engine) as session:
        assert _reserve(session, tenant, agent, job, clock + timedelta(seconds=41)).fencing_token == 2
        session.commit()
    opts["fencing_token"] = 2

    def job_reclaimed_while_downloaded(source_url):
        with Session(engine) as session:
            src = session.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.id == job,
            ).with_for_update(nowait=True))
            src.attempt_count = 3
            src.claimed_at = clock + timedelta(seconds=43)
            src.lease_expires_at = clock + timedelta(minutes=4)
            session.commit()
        return jpeg

    monkeypatch.setattr(staging, "_fetch_etsy_source", job_reclaimed_while_downloaded)
    denied = staging.capture_server_source_in_stages(**opts)
    assert denied.state in {"stale_fencing_token", "job_claim_not_verified"}
    with Session(engine) as session:
        assert "_cam_fenced_source_capture_v1" not in session.get(
            ProcessingJobModel, job,
        ).payload_json
        session.rollback()



def test_4c3g_capacity_stop_and_bad_source_do_not_write_manifest(
    ledger, tmp_path, monkeypatch,
):
    from app.modules.agent_harness import fenced_source_staging as staging

    engine, tenant, agent, job, clock = ledger
    url = "https://i.etsystatic.com/1234567/r/il/a.jpg"
    _install_4c3g_job_source(engine, tenant, agent, job, clock, url)
    opts = dict(
        session_factory=lambda: Session(engine),
        tenant_id=tenant, agent_id=agent, job_id=job,
        fencing_token=1, signing_key=b"x" * 32, storage_root=tmp_path,
        enabled=True, emergency_stop=False,
        now=clock + timedelta(seconds=1),
    )
    calls = []
    monkeypatch.setattr(staging, "_fetch_etsy_source", lambda u: (
        calls.append(u) or _jpg_for_capture()
    ))
    limited = threading.BoundedSemaphore(1)
    limited.acquire()
    monkeypatch.setattr(staging, "_CAPTURE_SLOTS", limited)
    assert staging.capture_server_source_in_stages(**opts).state == "source_capture_capacity_busy"
    assert calls == []
    limited.release()

    monkeypatch.setattr(staging, "_fetch_etsy_source", lambda u: b"not-an-image")
    assert staging.capture_server_source_in_stages(**opts).state == "source_image_invalid"
    assert list(tmp_path.iterdir()) == []
    with Session(engine) as session:
        source = session.get(ProcessingJobModel, job)
        assert "_cam_fenced_source_capture_v1" not in source.payload_json
        session.rollback()


def test_4c3f_review_reader_is_tenant_scoped_and_descriptor_verified(ledger, tmp_path):
    from app.modules.agent_harness.fenced_viewer import read_accepted_image_for_review

    engine, tenant, agent, job, clock = ledger
    image = _png()
    with Session(engine) as session:
        assert _reserve(session, tenant, agent, job, clock).state == "reserved"
        assert _prepare(session, tenant, agent, job, clock, image).state == "prepared"
        session.commit()
    with Session(engine) as session:
        assert read_accepted_image_for_review(
            session, tenant_id=tenant, job_id=job, storage_root=tmp_path,
            enabled=True, emergency_stop=False,
        ).state == "not_available"
        session.rollback()
    with Session(engine) as session:
        accepted = _accept(
            session, tenant, agent, job, clock + timedelta(seconds=1),
            image, tmp_path,
        )
        assert accepted.state == "accepted"
        session.commit()
    with Session(engine) as session:
        assert read_accepted_image_for_review(
            session, tenant_id=tenant, job_id=job, storage_root=tmp_path,
        ).state == "feature_disabled"
        assert read_accepted_image_for_review(
            session, tenant_id=tenant, job_id=job, storage_root=tmp_path,
            enabled=True,
        ).state == "emergency_stop"
        assert read_accepted_image_for_review(
            session, tenant_id="not-" + tenant, job_id=job, storage_root=tmp_path,
            enabled=True, emergency_stop=False,
        ).state == "not_found"
        verified = read_accepted_image_for_review(
            session, tenant_id=tenant, job_id=job, storage_root=tmp_path,
            enabled=True, emergency_stop=False,
        )
        assert verified.ready
        assert verified.png == image
        assert verified.output_sha256 == hashlib.sha256(image).hexdigest()
        session.rollback()
    output_path = tmp_path / accepted.storage_relative_path
    output_path.write_bytes(b"replaced")
    with Session(engine) as session:
        assert read_accepted_image_for_review(
            session, tenant_id=tenant, job_id=job, storage_root=tmp_path,
            enabled=True, emergency_stop=False,
        ).state == "not_verified"
        assert session.get(ProcessingJobModel, job).status == "completed"
        session.rollback()


def test_png_result_acceptance_is_same_transaction_as_processing_job(ledger, tmp_path):
    engine, tenant, agent, job, clock = ledger
    png = _png()
    with Session(engine) as session:
        assert _reserve(session, tenant, agent, job, clock).state == "reserved"
        session.commit()
    with Session(engine) as session:
        prepared = _prepare(session, tenant, agent, job, clock + timedelta(seconds=1), png)
        assert prepared.state == "prepared"
        assert prepared.output_sha256 == hashlib.sha256(png).hexdigest()
        session.commit()

    with Session(engine) as session:
        accepted = _accept(session, tenant, agent, job, clock + timedelta(seconds=2), png, tmp_path)
        assert accepted.state == "accepted"
        assert accepted.receipt_id == prepared.receipt_id
        # The caller could still roll back until its explicit commit.
        session.commit()
    target = tmp_path / accepted.storage_relative_path
    assert target.is_file() and target.read_bytes() == png
    with Session(engine) as session:
        source = session.get(ProcessingJobModel, job)
        receipt = session.get(AgentImageOutputReceiptModel, (tenant, job))
        slot = session.get(AgentExecutorSlotModel, (tenant, agent))
        assert source.status == "completed"
        assert source.lease_expires_at is None
        assert source.payload_json["result"]["receipt_id"] == prepared.receipt_id
        assert source.payload_json["result"]["print_ready"] is False
        assert source.payload_json["result"]["manual_qa_required"] is True
        assert source.payload_json["result"]["rights_status"] == "unverified"
        assert receipt.status == "accepted" and receipt.accepted_at is not None
        assert receipt.fencing_token == 1 and receipt.claim_attempt_count == 1
        assert slot.fencing_token == 1  # separate reconciliation releases slot

    with Session(engine) as session:
        replay = _accept(session, tenant, agent, job, clock + timedelta(seconds=3), png, tmp_path)
        assert replay.state == "accepted_replay"
        assert replay.receipt_id == prepared.receipt_id
        assert _accept(
            session, tenant, agent, job, clock + timedelta(seconds=3), _png("red"), tmp_path,
        ).state == "stale_or_conflicting_replay"
        session.rollback()
    target.unlink()
    with Session(engine) as session:
        assert _accept(
            session, tenant, agent, job, clock + timedelta(seconds=4), png, tmp_path,
        ).state == "storage_not_verified"
        session.rollback()


def test_prepared_receipt_is_idempotent_but_never_replaced(ledger):
    engine, tenant, agent, job, clock = ledger
    png = _png()
    with Session(engine) as session:
        assert _reserve(session, tenant, agent, job, clock).state == "reserved"
        session.commit()
    with Session(engine) as session:
        first = _prepare(session, tenant, agent, job, clock, png)
        assert first.state == "prepared"
        session.commit()
    with Session(engine) as session:
        same = _prepare(session, tenant, agent, job, clock + timedelta(seconds=1), png)
        assert same.state == "already_prepared"
        assert same.receipt_id == first.receipt_id
        assert _prepare(
            session, tenant, agent, job, clock + timedelta(seconds=1), _png("red"),
        ).state == "receipt_conflict"
        assert _prepare(
            session, tenant, agent, job, clock + timedelta(seconds=1), png,
            source_sha256="b" * 64,
        ).state == "receipt_conflict"
        assert _prepare(
            session, tenant, agent, job, clock + timedelta(seconds=1), png,
            engine=[],
        ).state == "invalid_receipt"
        assert _prepare(
            session, tenant, agent, job, clock + timedelta(seconds=1), png,
            fencing_token=0,
        ).state == "stale_fencing_token"
        assert _prepare(
            session, tenant, agent, job, clock + timedelta(seconds=1), png,
            agent_id="other-agent",
        ).state == "job_claim_not_verified"
        session.rollback()


def test_concurrent_prepare_serializes_and_conflicts_stay_immutable(ledger):
    engine, tenant, agent, job, clock = ledger
    data = _png()
    with Session(engine) as session:
        assert _reserve(session, tenant, agent, job, clock).state == "reserved"
        session.commit()
    barrier = threading.Barrier(2)

    def worker():
        with Session(engine) as session:
            barrier.wait(timeout=15)
            value = _prepare(session, tenant, agent, job, clock, data)
            session.commit()
            return value

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(worker), pool.submit(worker)
        results = [first.result(timeout=30), second.result(timeout=30)]
    assert {r.state for r in results} == {"prepared", "already_prepared"}
    assert results[0].receipt_id == results[1].receipt_id
    with Session(engine) as session:
        assert session.get(AgentImageOutputReceiptModel, (tenant, job)).status == "prepared"


def test_crash_after_fsync_before_commit_is_exactly_recoverable(ledger, tmp_path):
    engine, tenant, agent, job, clock = ledger
    data = _png()
    with Session(engine) as session:
        assert _reserve(session, tenant, agent, job, clock).state == "reserved"
        assert _prepare(session, tenant, agent, job, clock, data).state == "prepared"
        session.commit()
    with Session(engine) as session:
        result = _accept(session, tenant, agent, job, clock + timedelta(seconds=1), data, tmp_path)
        assert result.state == "accepted"
        stored = tmp_path / result.storage_relative_path
        assert stored.read_bytes() == data
        # Simulate process crash after atomic fsync but before DB COMMIT.
        session.rollback()
    assert stored.is_file()
    with Session(engine) as session:
        assert session.get(ProcessingJobModel, job).status == "processing"
        assert session.get(AgentImageOutputReceiptModel, (tenant, job)).status == "prepared"
    with Session(engine) as session:
        resumed = _accept(session, tenant, agent, job, clock + timedelta(seconds=2), data, tmp_path)
        assert resumed.state == "accepted"
        assert resumed.receipt_id == result.receipt_id
        session.commit()
    assert stored.read_bytes() == data


def test_fail_closed_for_provenance_storage_and_corrupt_bytes(ledger, tmp_path):
    engine, tenant, agent, job, clock = ledger
    data = _png()
    with Session(engine) as session:
        assert _reserve(session, tenant, agent, job, clock).state == "reserved"
        assert _prepare(session, tenant, agent, job, clock, data).state == "prepared"
        session.commit()
    with Session(engine) as session:
        assert _accept(
            session, tenant, agent, job, clock + timedelta(seconds=1), data,
            tmp_path, provenance_verified=False,
        ).state == "provenance_not_verified"
        assert _accept(
            session, tenant, agent, job, clock + timedelta(seconds=1), data,
            tmp_path, output_sha256="f" * 64,
        ).state == "artifact_integrity_error"
        assert _accept(
            session, tenant, agent, job, clock + timedelta(seconds=1), b"not a png",
            tmp_path, output_sha256=hashlib.sha256(b"not a png").hexdigest(),
        ).state == "artifact_integrity_error"
        assert _accept(
            session, tenant, agent, job, clock + timedelta(seconds=1), data,
            tmp_path / "missing",
        ).state == "storage_not_verified"
        assert _accept(
            session, tenant, agent, job, clock + timedelta(seconds=1), data,
            tmp_path, source_sha256="b" * 64,
        ).state == "receipt_conflict"
        assert _accept(
            session, tenant, agent, job, clock + timedelta(seconds=1), data,
            tmp_path, engine={},
        ).state == "invalid_receipt"
        session.rollback()
    assert list(tmp_path.iterdir()) == []
    with Session(engine) as session:
        assert session.get(ProcessingJobModel, job).status == "processing"
        assert session.get(AgentImageOutputReceiptModel, (tenant, job)).status == "prepared"


def test_conflicting_preexisting_blob_cannot_be_overwritten(ledger, tmp_path):
    engine, tenant, agent, job, clock = ledger
    png = _png()
    with Session(engine) as session:
        assert _reserve(session, tenant, agent, job, clock).state == "reserved"
        prepared = _prepare(session, tenant, agent, job, clock, png)
        assert prepared.state == "prepared"
        session.commit()
    destination = tmp_path / prepared.storage_relative_path
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"this name is already occupied by different bytes")
    with Session(engine) as session:
        assert _accept(
            session, tenant, agent, job, clock + timedelta(seconds=1),
            png, tmp_path,
        ).state == "storage_not_verified"
        session.rollback()
    assert destination.read_bytes() == b"this name is already occupied by different bytes"
    with Session(engine) as session:
        assert session.get(ProcessingJobModel, job).status == "processing"
        assert session.get(AgentImageOutputReceiptModel, (tenant, job)).status == "prepared"


def test_symlinked_storage_root_fails_closed(ledger, tmp_path):
    engine, tenant, agent, job, clock = ledger
    png = _png()
    with Session(engine) as session:
        assert _reserve(session, tenant, agent, job, clock).state == "reserved"
        assert _prepare(session, tenant, agent, job, clock, png).state == "prepared"
        session.commit()
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(trusted, target_is_directory=True)
    with Session(engine) as session:
        assert _accept(
            session, tenant, agent, job, clock + timedelta(seconds=1),
            png, alias,
        ).state == "storage_not_verified"
        session.rollback()
    assert list(trusted.iterdir()) == []


def test_new_claim_cannot_overwrite_prepared_old_epoch(ledger):
    engine, tenant, agent, job, clock = ledger
    data = _png()
    with Session(engine) as session:
        assert _reserve(session, tenant, agent, job, clock).state == "reserved"
        assert _prepare(session, tenant, agent, job, clock, data).state == "prepared"
        session.commit()
    new_clock = clock + timedelta(seconds=40)
    with Session(engine) as session:
        source = session.get(ProcessingJobModel, job)
        source.attempt_count = 2
        source.claimed_at = new_clock
        source.lease_expires_at = new_clock + timedelta(minutes=2)
        session.commit()
    with Session(engine) as session:
        assert _reserve(session, tenant, agent, job, new_clock).fencing_token == 2
        session.commit()
    with Session(engine) as session:
        result = _prepare(
            session, tenant, agent, job, new_clock, _png("red"), fencing_token=2,
        )
        assert result.state == "receipt_conflict"
        session.rollback()
    with Session(engine) as session:
        receipt = session.get(AgentImageOutputReceiptModel, (tenant, job))
        assert receipt.claim_attempt_count == 1 and receipt.fencing_token == 1