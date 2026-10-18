"""Internal future result receipt service: transaction, fencing, replay."""
import hashlib
import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel, BrandWorkerModel, now_utc
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.scan_receipt_model import BrandScanFailureReceiptModel
from app.modules.branding_dna.scan_result_receipt_model import BrandScanResultReceiptModel
from app.modules.branding_dna.scan_result_contract import ResultManifest
from app.modules.branding_dna.scan_result_receipt_service import (
    FutureResultPacket, _canonical_packet, persist_quarantined_future_result,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandWorkerModel.__table__,
        BrandListingSnapshotModel.__table__, BrandScanTaskModel.__table__,
        BrandScanFailureReceiptModel.__table__, BrandScanResultReceiptModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def fixture(db, tenant="tenant-a"):
    worker = BrandWorkerModel(id=str(uuid4()), tenant_id=tenant, label="future test",
                              status="idle", enabled=True)
    shop = BrandShopModel(id=str(uuid4()), tenant_id=tenant, platform="etsy",
                          region="US", canonical_shop_key=str(uuid4()),
                          storefront_url="https://www.etsy.com/shop/example",
                          display_name="example", access_mode="authorized",
                          created_by="operator")
    db.add_all((worker, shop))
    db.flush()
    snap = BrandListingSnapshotModel(
        id=str(uuid4()), tenant_id=tenant, shop_id=shop.id,
        listing_key="Listing_001", source_revision="rev-1",
        export_reference="source-1", title="Example listing",
        category="hats", source_kind="authorized_export",
        evidence_state="imported_unverified", coverage_state="unknown",
        captured_at=now_utc(), imported_by="operator", content_sha256="a"*64,
    )
    db.add(snap)
    db.flush()
    task = BrandScanTaskModel(
        id=str(uuid4()), tenant_id=tenant, shop_id=shop.id, snapshot_id=snap.id,
        task_kind="verify_listing_evidence", status="leased", attempts=1,
        max_attempts=3, lease_generation=1, lease_id=str(uuid4()),
        agent_id=worker.id, lease_until=now_utc()+timedelta(seconds=120),
        created_by="operator",
    )
    db.add(task)
    db.commit()
    return worker,shop,snap,task


def packet(task,shop,snap,override=None):
    gallery=[dict(sequence_index=0, asset_sha256="b"*64,
                  source_url_sha256="c"*64, role="hero",rights_state="unknown")]
    manifest=dict(listing_key=snap.listing_key,observed_revision="rev-2",
                  captured_at=now_utc().isoformat(),page_count=1,images=gallery)
    payload=dict(client_receipt_id=str(uuid4()),client_payload_sha256="0"*64,
                 task_id=task.id,lease_id=task.lease_id,
                 lease_generation=task.lease_generation,
                 shop_id=shop.id,snapshot_id=snap.id,manifest=manifest)
    if override:
        payload.update(override)
    preliminary=FutureResultPacket.model_validate(payload)
    # Independently duplicate worker JSON.stringify's canonical field order.
    details=preliminary.manifest
    clock=details.captured_at.isoformat(timespec="milliseconds").replace("+00:00","Z")
    raw={
        "task_id":task.id, "lease_id":preliminary.lease_id.__str__(),
        "lease_generation":task.lease_generation,
        "shop_id":shop.id,"snapshot_id":snap.id,
        "manifest":{
            "listing_key":details.listing_key,
            "observed_revision":details.observed_revision,
            "captured_at":clock,"page_count":details.page_count,
            "images":[{
                "sequence_index":im.sequence_index,
                "asset_sha256":im.asset_sha256,
                "source_url_sha256":im.source_url_sha256,
                "role":im.role,
                "rights_state":im.rights_state,
            } for im in details.images],
        },
    }
    digest=hashlib.sha256(json.dumps(raw,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    return preliminary.model_copy(update={"client_payload_sha256":digest})


def count(db):
    return db.scalar(select(func.count()).select_from(BrandScanResultReceiptModel))


def save(db, worker,body,tenant=None):
    return persist_quarantined_future_result(
        db,tenant_id=tenant or worker.tenant_id,agent_id=worker.id,body=body,
    )


def test_transactional_quarantine_replay_and_no_evidence_promotion(db):
    worker,shop,snap,task=fixture(db)
    body=packet(task,shop,snap)
    first=save(db,worker,body)
    assert first["accepted"] is True and first["persisted"] is True
    assert first["receipt_issued"] is True and first["replayed"] is False
    assert first["status"] == "quarantined_unverified"
    assert first["scan_enabled"] is False and first["provenance_verified"] is False
    assert first["marketplace_authorization_verified"] is False
    assert set(first)=={
        "accepted","client_receipt_id","client_payload_sha256","task_id",
        "lease_id","lease_generation","shop_id","snapshot_id","status",
        "receipt_id","replayed","persisted","receipt_issued",
        "provenance_verified","marketplace_authorization_verified","scan_enabled",
    }
    row=db.scalar(select(BrandScanResultReceiptModel))
    assert row.manifest_sha256 and len(row.manifest_sha256)==64
    assert row.client_payload_sha256==body.client_payload_sha256
    assert "http" not in row.manifest_json and "token" not in row.manifest_json
    assert "media_bytes" not in row.manifest_json
    assert count(db)==1
    db.refresh(task)
    db.refresh(snap)
    assert task.status=="blocked" and task.last_error_code=="manual_check_required"
    assert task.lease_id is None and task.agent_id is None
    assert snap.evidence_state=="imported_unverified"
    second=save(db,worker,body)
    assert second=={**first,"replayed":True}
    assert count(db)==1


def test_stale_expired_mismatched_lease_never_writes(db):
    worker,shop,snap,task=fixture(db)
    body=packet(task,shop,snap)
    task.lease_until=now_utc()-timedelta(minutes=1)
    db.commit()
    with pytest.raises(HTTPException) as err:
        save(db,worker,body)
    assert err.value.status_code==409 and count(db)==0
    task.lease_until=now_utc()+timedelta(minutes=2)
    task.lease_generation=2
    db.commit()
    with pytest.raises(HTTPException) as err:
        save(db,worker,body)
    assert err.value.status_code==409 and count(db)==0


def test_conflicting_replay_and_cross_tenant_rejected(db):
    worker,shop,snap,task=fixture(db)
    body=packet(task,shop,snap)
    with pytest.raises(HTTPException):
        save(db,worker,body,tenant="other-tenant")
    with pytest.raises(HTTPException):
        save(db,worker,body.model_copy(update={"client_payload_sha256":"f"*64}))
    with pytest.raises(HTTPException):
        save(db,worker,body.model_copy(update={"snapshot_id":uuid4()}))
    assert count(db)==0
    first=save(db,worker,body)
    changed=body.model_copy(update={"client_receipt_id":uuid4()})
    with pytest.raises(HTTPException):
        save(db,worker,changed)
    assert count(db)==1
    assert db.scalar(select(BrandScanResultReceiptModel)).id==first["receipt_id"]


def test_revoked_worker_replay_denied_and_conflicting_existing_failure(db):
    worker,shop,snap,task=fixture(db)
    body=packet(task,shop,snap)
    save(db,worker,body)
    worker.enabled=False
    db.commit()
    with pytest.raises(HTTPException) as err:
        save(db,worker,body)
    assert err.value.status_code==409
    assert count(db)==1

    worker2,shop2,snap2,task2=fixture(db,tenant="other")
    body2=packet(task2,shop2,snap2)
    db.add(BrandScanFailureReceiptModel(
        tenant_id="other",task_id=task2.id,lease_generation=1,
        lease_id=task2.lease_id,agent_id=worker2.id,
        error_code="rate_limited",status="retry",
    ))
    db.commit()
    with pytest.raises(HTTPException) as err:
        save(db,worker2,body2)
    assert err.value.status_code==409
    assert count(db)==1


def test_cross_language_canonical_hash_matches_node_worker_golden():
    """Golden produced with Node validateFutureResultManifest + JSON.stringify."""
    raw = {
        "client_receipt_id": "12345678-1111-4111-8111-123456789012",
        "client_payload_sha256":
            "b5caf0640b76999e286a57dfa34cca3c1fef8a1968446a1bfd5fdadfccdd8211",
        "task_id": "12345678-2222-4222-8222-123456789012",
        "lease_id": "12345678-3333-4333-8333-123456789012",
        "lease_generation": 1,
        "shop_id": "12345678-4444-4444-8444-123456789012",
        "snapshot_id": "12345678-5555-4555-8555-123456789012",
        "manifest": {
            "listing_key": "Listing_001",
            "observed_revision": "rev-2",
            "captured_at": "2026-10-18T04:55:00+00:00",
            "page_count": 1,
            "images": [{
                "sequence_index": 0,
                "asset_sha256": "b"*64,
                "source_url_sha256": "c"*64,
                "role": "hero",
                "rights_state": "unknown",
            }],
        },
    }
    canonical, manifest_json, digest = _canonical_packet(
        FutureResultPacket.model_validate(raw)
    )
    assert digest == raw["client_payload_sha256"]
    assert canonical["manifest"]["captured_at"] == "2026-10-18T04:55:00.000Z"
    assert len(manifest_json) < 8192


def test_lease_snapshot_key_and_access_declared_checks(db):
    worker,shop,snap,task=fixture(db)
    body=packet(task,shop,snap)
    shop.access_mode="public_observation"
    db.commit()
    with pytest.raises(HTTPException) as err:
        save(db,worker,body)
    assert err.value.status_code==409
    assert count(db)==0
    shop.access_mode="authorized"
    db.commit()
    wrong=ResultManifest.model_validate({
        **body.manifest.model_dump(),"listing_key":"WrongListing",
    })
    body2=body.model_copy(update={"manifest":wrong})
    with pytest.raises(HTTPException):
        save(db,worker,body2)
    assert count(db)==0
