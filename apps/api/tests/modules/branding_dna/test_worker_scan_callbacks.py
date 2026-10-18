"""HTTP contract tests for non-dispatchable fenced worker scan callbacks."""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.branding_dna.evidence_model import BrandListingImageModel, BrandListingSnapshotModel
from app.modules.branding_dna.evidence_router import GalleryImport, ListingImport, import_listing
from app.modules.branding_dna.model import BrandShopModel, BrandWorkerModel
from app.modules.branding_dna.router import ShopCreate, add_shop
from app.modules.branding_dna.scan_callback_router import router as callback_router
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.scan_receipt_model import BrandScanFailureReceiptModel
from app.modules.branding_dna.task_router import TaskStage, stage_task
from app.modules.branding_dna.task_service import claim_next_task
from app.modules.branding_dna.worker_router import (
    WorkerRegister, disable_worker, register_worker, router as worker_router,
)


def principal(tenant="tenant-a"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id="operator")


@pytest.fixture
def setup():
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandWorkerModel.__table__,
        BrandListingSnapshotModel.__table__, BrandListingImageModel.__table__,
        BrandScanTaskModel.__table__, BrandScanFailureReceiptModel.__table__,
    ])
    app = FastAPI()
    app.include_router(callback_router)
    app.include_router(worker_router)

    def session_provider():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = session_provider
    with TestClient(app) as client:
        yield engine, client
    engine.dispose()


def lease_for_test(engine, *, tenant="tenant-a", shop_name="OwnHats"):
    """Local fixture only. Production offers no staged -> pending path."""
    with Session(engine) as session:
        shop = add_shop(ShopCreate(
            platform="amazon", storefront_url="https://www.amazon.com/stores/"+shop_name,
            access_mode="authorized",
        ), session=session, principal=principal(tenant))
        snapshot = import_listing(shop["id"], ListingImport(
            listing_key="test-listing", source_revision="rev1", export_reference="fixture",
            title="One hat", category="hats",
            captured_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
            images=[GalleryImport(sequence_index=0, asset_sha256="a"*64,
                                 source_url_sha256="b"*64)],
        ), session=session, principal=principal(tenant))
        task = stage_task(
            shop["id"], TaskStage(snapshot_id=snapshot["item"]["id"]),
            session=session, principal=principal(tenant),
        )
        worker = register_worker(
            WorkerRegister(label="Fixture Worker"), Response(),
            session=session, principal=principal(tenant),
        )
        # Explicit test-only activation -- NEVER surfaced through HTTP.
        row = session.get(BrandScanTaskModel, task["item"]["id"])
        row.status = "pending"
        session.commit()
        lease = claim_next_task(
            session, tenant_id=tenant, agent_id=worker["id"],
            dispatch_authorized=True, adapter_verified=True,
        )
        assert lease
        return shop["id"], task["item"]["id"], worker, lease


def url(agent_id, task_id, kind):
    return f"/api/v1/branding-dna/workers/{agent_id}/scan-tasks/{task_id}/{kind}"


def bearer(token):
    return {"Authorization": "Bearer " + token}


def packet(lease, *, checkpoint_seq=1, error_code=None):
    data = {
        "lease_id": lease["lease_id"],
        "lease_generation": lease["lease_generation"],
    }
    if error_code is None:
        data["checkpoint_seq"] = checkpoint_seq
    else:
        data["error_code"] = error_code
    return data


def test_staged_queue_never_dispatches_and_unleased_callbacks_conflict(setup):
    engine, client = setup
    shop_id, task_id, worker, lease = lease_for_test(engine)
    poll = client.post(
        f"/api/v1/branding-dna/workers/{worker['id']}/lease",
        headers=bearer(worker["agent_token"]),
    )
    assert poll.status_code == 200
    assert poll.json()["job"] is None and poll.json()["scan_enabled"] is False
    # Leased state exists only via the deliberately internal test fixture.
    assert "source_url" not in lease and "token" not in lease
    forged = client.post(url(worker["id"], task_id, "checkpoint"),
        headers=bearer(worker["agent_token"]),
        json={**packet(lease), "lease_id": "00000000-0000-4000-8000-000000000001"})
    assert forged.status_code == 409


def test_checkpoint_monotonic_ack_without_browser_or_evidence_mutation(setup):
    engine, client = setup
    shop_id, task_id, worker, lease = lease_for_test(engine)
    address = url(worker["id"], task_id, "checkpoint")
    headers = bearer(worker["agent_token"])
    first = client.post(address, headers=headers, json=packet(lease, checkpoint_seq=4))
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    assert first.json()["checkpoint_seq"] == 4 and first.json()["scan_enabled"] is False
    with Session(engine) as session:
        expires = session.get(BrandScanTaskModel, task_id).lease_until
    repeat = client.post(address, headers=headers, json=packet(lease, checkpoint_seq=4))
    assert repeat.status_code == 200 and repeat.json()["checkpoint_seq"] == 4
    with Session(engine) as session:
        task = session.get(BrandScanTaskModel, task_id)
        assert task.lease_until == expires
        snapshot = session.get(BrandListingSnapshotModel, task.snapshot_id)
        assert snapshot.evidence_state == "imported_unverified"
        assert task.status == "leased"
    assert client.post(address, headers=headers,
                       json=packet(lease, checkpoint_seq=3)).status_code == 409


def test_worker_token_tenant_and_fencing_all_fail_closed(setup):
    engine, client = setup
    shop_id, task_id, worker, lease = lease_for_test(engine)
    address = url(worker["id"], task_id, "checkpoint")
    data = packet(lease)
    assert client.post(address, json=data).status_code == 401
    assert client.post(address, headers=bearer("bad"), json=data).status_code == 401
    with Session(engine) as session:
        other = register_worker(WorkerRegister(label="Other Agent"),
                                Response(), session=session, principal=principal())
        other_tenant = register_worker(WorkerRegister(label="Another Tenant"),
                                Response(), session=session, principal=principal("tenant-b"))
    assert client.post(url(other["id"], task_id, "checkpoint"),
                       headers=bearer(other["agent_token"]), json=data).status_code == 409
    assert client.post(url(other_tenant["id"], task_id, "checkpoint"),
                       headers=bearer(other_tenant["agent_token"]), json=data).status_code == 409
    assert client.post(address, headers=bearer(worker["agent_token"]),
        json={**data, "lease_generation": data["lease_generation"] + 1}).status_code == 409
    assert client.post(address, headers=bearer(worker["agent_token"]),
        json={**data, "checkpoint_seq": 1, "source_url": "https://bad.example"}).status_code == 422
    assert client.post(address, headers=bearer(worker["agent_token"]),
        json={**data, "checkpoint_seq": "1"}).status_code == 422
    assert client.post(address, headers=bearer(worker["agent_token"]),
        json={**data, "checkpoint_seq": 1000001}).status_code == 422


def test_failure_classification_retry_and_replay_deny(setup):
    engine, client = setup
    shop_id, task_id, worker, lease = lease_for_test(engine)
    headers = bearer(worker["agent_token"])
    address = url(worker["id"], task_id, "failure")
    bad = client.post(address, headers=headers, json=packet(lease, error_code="shell_command"))
    assert bad.status_code == 422
    safe = client.post(address, headers=headers,
                       json=packet(lease, error_code="temporary_network_error"))
    assert safe.status_code == 200, safe.text
    assert safe.json()["status"] == "retry" and safe.json()["scan_enabled"] is False
    assert safe.json()["retry_after"]
    assert safe.headers["cache-control"] == "no-store"
    replay = client.post(address, headers=headers,
                         json=packet(lease, error_code="temporary_network_error"))
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["retry_after"] == safe.json()["retry_after"]
    assert client.post(address, headers=headers,
                       json=packet(lease, error_code="rate_limited")).status_code == 409
    assert client.post(url(worker["id"], task_id, "checkpoint"), headers=headers,
                       json=packet(lease)).status_code == 409
    with Session(engine) as session:
        task = session.get(BrandScanTaskModel, task_id)
        assert task.status == "retry"
        assert task.lease_id is None and task.agent_id is None


@pytest.mark.parametrize("code", ["parser_changed", "manual_check_required"])
def test_manual_blocks_never_requeue(setup, code):
    engine, client = setup
    shop_id, task_id, worker, lease = lease_for_test(engine)
    result = client.post(url(worker["id"], task_id, "failure"),
        headers=bearer(worker["agent_token"]), json=packet(lease, error_code=code))
    assert result.status_code == 200
    assert result.json()["status"] == "blocked"
    assert result.json()["retry_after"] is None


def test_terminal_receipt_is_durable_and_old_replay_does_not_mutate_new_lease(setup):
    engine, client = setup
    shop_id, task_id, worker, lease1 = lease_for_test(engine)
    address = url(worker["id"], task_id, "failure")
    headers = bearer(worker["agent_token"])
    submitted = packet(lease1, error_code="rate_limited")
    accepted = client.post(address, headers=headers, json=submitted)
    assert accepted.status_code == 200 and accepted.json()["replayed"] is False
    original_retry = accepted.json()["retry_after"]
    # Force readiness solely in test fixture so a second lease can be claimed.
    with Session(engine) as session:
        row = session.get(BrandScanTaskModel, task_id)
        row.retry_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
        lease2 = claim_next_task(
            session, tenant_id="tenant-a", agent_id=worker["id"],
            dispatch_authorized=True, adapter_verified=True,
        )
        assert lease2 and lease2["lease_generation"] == lease1["lease_generation"] + 1
        before = (row.attempts, row.status, row.lease_generation, row.lease_id)
    replay = client.post(address, headers=headers, json=submitted)
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["retry_after"] == original_retry
    with Session(engine) as session:
        row = session.get(BrandScanTaskModel, task_id)
        assert (row.attempts, row.status, row.lease_generation, row.lease_id) == before
        assert session.query(BrandScanFailureReceiptModel).count() == 1
    assert client.post(address, headers=headers,
        json={**submitted, "lease_id":"00000000-0000-4000-8000-000000000111"}).status_code == 409
    assert client.post(address, headers=headers,
        json={**submitted, "lease_generation":lease2["lease_generation"]}).status_code == 409


def test_receipt_replay_requires_live_worker_token_even_after_commit(setup):
    engine, client = setup
    _, task_id, worker, lease = lease_for_test(engine)
    address = url(worker["id"], task_id, "failure")
    headers = bearer(worker["agent_token"])
    data = packet(lease, error_code="manual_check_required")
    assert client.post(address, headers=headers, json=data).status_code == 200
    assert client.post(address, headers=headers, json=data).json()["replayed"] is True
    with Session(engine) as session:
        disable_worker(worker["id"], session=session, principal=principal())
    assert client.post(address, headers=headers, json=data).status_code == 401


def test_revoked_worker_and_access_grant_fail_closed(setup):
    engine, client = setup
    shop_id, task_id, worker, lease = lease_for_test(engine)
    address = url(worker["id"], task_id, "checkpoint")
    with Session(engine) as session:
        shop = session.get(BrandShopModel, shop_id)
        shop.access_mode = "public_observation"
        session.commit()
    assert client.post(address, headers=bearer(worker["agent_token"]),
                       json=packet(lease)).status_code == 409
    with Session(engine) as session:
        disable_worker(worker["id"], session=session, principal=principal())
    assert client.post(address, headers=bearer(worker["agent_token"]),
                       json=packet(lease)).status_code == 401


def test_expired_lease_rejects_stale_callback(setup):
    engine, client = setup
    shop_id, task_id, worker, lease = lease_for_test(engine)
    with Session(engine) as session:
        task = session.get(BrandScanTaskModel, task_id)
        task.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    answer = client.post(url(worker["id"], task_id, "checkpoint"),
        headers=bearer(worker["agent_token"]), json=packet(lease))
    assert answer.status_code == 409
