"""BD-01 staged queue, simulated lease fencing and scan-off safety tests."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.evidence_model import BrandListingImageModel, BrandListingSnapshotModel
from app.modules.branding_dna.evidence_router import GalleryImport, ListingImport, import_listing
from app.modules.branding_dna.model import BrandShopModel, BrandWorkerModel
from app.modules.branding_dna.router import ShopCreate, add_shop
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.task_router import TaskStage, cancel_staged_task, list_scan_tasks, stage_task
from app.modules.branding_dna.task_service import (
    checkpoint_task, claim_next_task, release_for_retry,
)
from app.modules.branding_dna.worker_router import (
    WorkerRegister, register_worker, worker_lease,
)

START = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)


def principal(tenant="tenant-a"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id="user-a")


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__,
        BrandWorkerModel.__table__,
        BrandListingSnapshotModel.__table__,
        BrandListingImageModel.__table__,
        BrandScanTaskModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def setup_data(db, tenant="tenant-a", access_mode="authorized", name="HatsA"):
    shop = add_shop(ShopCreate(
        platform="etsy", storefront_url=f"https://www.etsy.com/shop/{name}",
        access_mode=access_mode), session=db, principal=principal(tenant))
    if access_mode != "public_observation":
        listing = ListingImport(
            listing_key="listing-1", source_revision="r1", export_reference="batch-1",
            title="Hats Listing", category="hats", captured_at=START,
            images=[GalleryImport(sequence_index=0, asset_sha256="a"*64,
                                 source_url_sha256="b"*64)],
        )
        snapshot = import_listing(shop["id"], listing, session=db, principal=principal(tenant))
        return shop["id"], snapshot["item"]["id"]
    return shop["id"], None


def stage(db, shop_id, snapshot_id, tenant="tenant-a"):
    return stage_task(shop_id, TaskStage(snapshot_id=snapshot_id),
                      session=db, principal=principal(tenant))


def test_staged_idempotent_and_no_worker_scan(db):
    shop_id, snapshot_id = setup_data(db)
    first = stage(db, shop_id, snapshot_id)
    assert first["item"]["status"] == "staged"
    assert first["dispatchable"] is False and first["scan_enabled"] is False
    second = stage(db, shop_id, snapshot_id)
    assert second["replayed"] and second["item"]["id"] == first["item"]["id"]
    assert db.query(BrandScanTaskModel).count() == 1
    results = list_scan_tasks(shop_id, 25, 0, session=db, principal=principal())
    assert results["count"] == 1 and results["items"][0]["status"] == "staged"
    agent = register_worker(WorkerRegister(label="BDNA Worker"), Response(), session=db, principal=principal())
    assert worker_lease(agent["id"], authorization="Bearer " + agent["agent_token"],
                        session=db)["job"] is None
    assert claim_next_task(db, tenant_id="tenant-a", agent_id=agent["id"],
                           dispatch_authorized=False, adapter_verified=True) is None
    assert claim_next_task(db, tenant_id="tenant-a", agent_id=agent["id"],
                           dispatch_authorized=True, adapter_verified=True) is None


def test_cross_tenant_and_public_shop_cannot_stage(db):
    shop_id, snapshot_id = setup_data(db)
    other_shop, other_snapshot = setup_data(db, tenant="tenant-b", name="HatsB")
    for method in (
        lambda: stage(db, shop_id, snapshot_id, "tenant-b"),
        lambda: stage(db, other_shop, other_snapshot, "tenant-a"),
        lambda: list_scan_tasks(shop_id, 25, 0, session=db, principal=principal("tenant-b")),
        lambda: stage(db, shop_id, other_snapshot),
    ):
        with pytest.raises(HTTPException) as err:
            method()
        assert err.value.status_code == 404
    public_shop, _ = setup_data(db, name="PublicHats", access_mode="public_observation")
    with pytest.raises(HTTPException) as err:
        stage(db, public_shop, snapshot_id)
    assert err.value.status_code == 403


def test_cancel_only_staged_and_replayed_cancel_stays_cancelled(db):
    shop_id, snapshot_id = setup_data(db)
    task = stage(db, shop_id, snapshot_id)["item"]
    cancelled = cancel_staged_task(shop_id, task["id"], session=db, principal=principal())
    assert cancelled["item"]["status"] == "cancelled"
    assert stage(db, shop_id, snapshot_id)["item"]["status"] == "cancelled"
    with pytest.raises(HTTPException) as err:
        cancel_staged_task(shop_id, task["id"], session=db, principal=principal())
    assert err.value.status_code == 409
    with pytest.raises(HTTPException) as err:
        cancel_staged_task(shop_id, task["id"], session=db, principal=principal("tenant-b"))
    assert err.value.status_code == 404


def activate_for_unit_test(db, task_id):
    # There is intentionally no public API or production code path for this.
    task = db.get(BrandScanTaskModel, task_id)
    task.status = "pending"
    db.commit()


def worker(db, tenant="tenant-a"):
    result = register_worker(WorkerRegister(label="Test worker"), Response(),
                             session=db, principal=principal(tenant))
    return result["id"]


def test_fencing_monotonic_checkpoint_and_no_stale_callback(db):
    shop_id, snapshot_id = setup_data(db)
    task_id = stage(db, shop_id, snapshot_id)["item"]["id"]
    agent_id = worker(db)
    activate_for_unit_test(db, task_id)
    assert claim_next_task(db, tenant_id="tenant-a", agent_id=agent_id,
                           dispatch_authorized=True, adapter_verified=False, now=START) is None
    lease1 = claim_next_task(db, tenant_id="tenant-a", agent_id=agent_id,
                             dispatch_authorized=True, adapter_verified=True, now=START)
    assert lease1["lease_generation"] == 1 and lease1["attempt"] == 1
    assert "source_url" not in lease1
    assert "token" not in lease1
    common = dict(tenant_id="tenant-a", task_id=task_id, agent_id=agent_id,
                  lease_id=lease1["lease_id"], lease_generation=1)
    ack = checkpoint_task(db, **common, checkpoint_seq=5, now=START + timedelta(seconds=10))
    assert ack["checkpoint_seq"] == 5
    assert checkpoint_task(db, **common, checkpoint_seq=5,
                           now=START + timedelta(seconds=15))["checkpoint_seq"] == 5
    with pytest.raises(HTTPException) as err:
        checkpoint_task(db, **common, checkpoint_seq=3, now=START + timedelta(seconds=20))
    assert err.value.status_code == 409
    with pytest.raises(HTTPException) as err:
        checkpoint_task(db, **{**common, "tenant_id": "tenant-b"},
                        checkpoint_seq=6, now=START + timedelta(seconds=22))
    assert err.value.status_code == 409
    assert claim_next_task(db, tenant_id="tenant-a", agent_id=agent_id,
                           dispatch_authorized=True, adapter_verified=True,
                           now=START + timedelta(minutes=4)) is None
    assert db.get(BrandScanTaskModel, task_id).last_error_code == "lease_expired"
    lease2 = claim_next_task(db, tenant_id="tenant-a", agent_id=agent_id,
                             dispatch_authorized=True, adapter_verified=True,
                             now=START + timedelta(minutes=4, seconds=31))
    assert lease2["lease_generation"] == 2 and lease2["attempt"] == 2
    assert lease2["checkpoint_seq"] == 5
    with pytest.raises(HTTPException) as err:
        checkpoint_task(db, **common, checkpoint_seq=10,
                        now=START + timedelta(minutes=4, seconds=35))
    assert err.value.status_code == 409
    ack2 = checkpoint_task(db, **{**common, "lease_id": lease2["lease_id"],
                                  "lease_generation": 2},
                           checkpoint_seq=6, now=START + timedelta(minutes=4, seconds=40))
    assert ack2["checkpoint_seq"] == 6
    with pytest.raises(HTTPException) as err:
        release_for_retry(db, **common, error_code="temporary_network_error",
                          now=START + timedelta(minutes=4, seconds=45))
    assert err.value.status_code == 409


def test_bounded_retry_and_manual_block(db):
    shop_id, snapshot_id = setup_data(db)
    task_id = stage(db, shop_id, snapshot_id)["item"]["id"]
    agent_id = worker(db)
    activate_for_unit_test(db, task_id)
    at = START
    for attempt in (1, 2, 3):
        leased = claim_next_task(db, tenant_id="tenant-a", agent_id=agent_id,
                                 dispatch_authorized=True, adapter_verified=True, now=at)
        assert leased is not None and leased["attempt"] == attempt
        ended = release_for_retry(
            db, tenant_id="tenant-a", task_id=task_id, agent_id=agent_id,
            lease_id=leased["lease_id"], lease_generation=leased["lease_generation"],
            error_code="temporary_network_error", now=at + timedelta(seconds=2),
        )
        assert ended["status"] == ("retry" if attempt < 3 else "blocked")
        at += timedelta(seconds=180)
    assert claim_next_task(db, tenant_id="tenant-a", agent_id=agent_id,
                           dispatch_authorized=True, adapter_verified=True, now=at) is None
    assert db.get(BrandScanTaskModel, task_id).attempts == 3


def test_unknown_error_refused_and_parser_drift_blocked(db):
    shop_id, snapshot_id = setup_data(db)
    task_id = stage(db, shop_id, snapshot_id)["item"]["id"]
    agent_id = worker(db)
    activate_for_unit_test(db, task_id)
    lease = claim_next_task(db, tenant_id="tenant-a", agent_id=agent_id,
                            dispatch_authorized=True, adapter_verified=True, now=START)
    args = dict(tenant_id="tenant-a", task_id=task_id, agent_id=agent_id,
                lease_id=lease["lease_id"], lease_generation=1, now=START + timedelta(seconds=5))
    with pytest.raises(HTTPException) as err:
        release_for_retry(db, **args, error_code="raise_sql_error")
    assert err.value.status_code == 422
    blocked = release_for_retry(db, **args, error_code="parser_changed")
    assert blocked["status"] == "blocked"
    assert blocked["retry_after"] is None

def test_revoked_worker_cannot_ack_active_lease(db):
    shop_id, snapshot_id = setup_data(db)
    task_id = stage(db, shop_id, snapshot_id)["item"]["id"]
    agent_id = worker(db)
    activate_for_unit_test(db, task_id)
    lease = claim_next_task(db, tenant_id="tenant-a", agent_id=agent_id,
                            dispatch_authorized=True, adapter_verified=True, now=START)
    agent = db.get(BrandWorkerModel, agent_id)
    agent.enabled = False
    db.commit()
    with pytest.raises(HTTPException) as error:
        checkpoint_task(db, tenant_id="tenant-a", task_id=task_id,
                        agent_id=agent_id, lease_id=lease["lease_id"],
                        lease_generation=lease["lease_generation"],
                        checkpoint_seq=1, now=START + timedelta(seconds=10))
    assert error.value.status_code == 409


def test_expired_last_attempt_is_blocked_without_requeue(db):
    shop_id, snapshot_id = setup_data(db)
    task_id = stage(db, shop_id, snapshot_id)["item"]["id"]
    agent_id = worker(db)
    task = db.get(BrandScanTaskModel, task_id)
    task.status = "pending"
    task.attempts = 2
    db.commit()
    lease = claim_next_task(db, tenant_id="tenant-a", agent_id=agent_id,
                            dispatch_authorized=True, adapter_verified=True, now=START)
    assert lease["attempt"] == 3
    assert claim_next_task(db, tenant_id="tenant-a", agent_id=agent_id,
                           dispatch_authorized=True, adapter_verified=True,
                           now=START + timedelta(minutes=3)) is None
    task = db.get(BrandScanTaskModel, task_id)
    assert task.status == "blocked" and task.last_error_code == "lease_expired"
    assert task.lease_id is None and task.retry_after is None
