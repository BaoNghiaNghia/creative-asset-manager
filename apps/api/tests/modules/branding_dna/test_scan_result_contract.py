"""Read-only scan result manifest preflight: no ingest, no worker dispatch."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.evidence_model import BrandListingImageModel, BrandListingSnapshotModel
from app.modules.branding_dna.evidence_router import GalleryImport, ListingImport, import_listing
from app.modules.branding_dna.model import BrandShopModel, now_utc
from app.modules.branding_dna.router import ShopCreate, add_shop
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.task_router import TaskStage, stage_task
from app.modules.branding_dna.scan_result_router import PreviewScanResult, preview_scan_result
from app.modules.branding_dna.scan_result_contract import (
    ResultManifest, ObservedGalleryItem, manifest_digest, valid_capture_window,
)


def principal(tenant="tenant-a"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id="admin")


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandListingSnapshotModel.__table__,
        BrandListingImageModel.__table__, BrandScanTaskModel.__table__,
    ])
    with Session(engine) as db:
        yield db
    engine.dispose()


def make_task(db, *, tenant="tenant-a", name="ExampleHats", platform="etsy"):
    url = (f"https://www.etsy.com/shop/{name}" if platform == "etsy"
           else f"https://www.amazon.com/stores/{name}")
    shop = add_shop(ShopCreate(
        platform=platform, storefront_url=url, access_mode="authorized"),
        session=db, principal=principal(tenant),
    )
    listing = ListingImport(
        listing_key="Listing_001", source_revision="r1",
        export_reference="export-r1", title="Source hat", category="hats",
        captured_at=now_utc(),
        images=[GalleryImport(
            sequence_index=0, asset_sha256="a" * 64,
            source_url_sha256="b" * 64,
        )],
    )
    imported = import_listing(shop["id"], listing, session=db, principal=principal(tenant))
    snapshot_id = imported["item"]["id"]
    staged = stage_task(shop["id"], TaskStage(snapshot_id=snapshot_id),
                        session=db, principal=principal(tenant))
    return shop, snapshot_id, staged["item"]["id"]


def manifest(**changes):
    fields = {
        "listing_key": "Listing_001", "observed_revision": "r2",
        "captured_at": now_utc(), "page_count": 1,
        "images": [dict(sequence_index=0, asset_sha256="c" * 64,
                        source_url_sha256="d" * 64, role="hero",
                        rights_state="unknown")],
    }
    fields.update(changes)
    return ResultManifest.model_validate(fields)


def test_manifest_preview_is_replay_stable_without_writes(session):
    shop, snapshot_id, task_id = make_task(session)
    candidate = manifest()
    packet = PreviewScanResult(task_id=UUID(task_id), manifest=candidate)
    snapshot_before = session.get(BrandListingSnapshotModel, snapshot_id).content_sha256
    task_before = session.get(BrandScanTaskModel, task_id).status
    image_count = session.scalar(select(func.count()).select_from(BrandListingImageModel))
    response = Response()
    first = preview_scan_result(shop["id"], packet, response, session, principal())
    second = preview_scan_result(shop["id"], packet, Response(), session, principal())
    assert response.headers["cache-control"] == "no-store"
    assert first == second
    assert len(first["manifest_sha256"]) == 64
    assert first["gallery_items"] == 1 and first["page_count"] == 1
    assert first["evidence_state"] == "imported_unverified"
    assert first["rights_state"] == "unknown"
    assert first["provenance_verified"] is False
    assert first["marketplace_authorization_verified"] is False
    assert first["receipt_issued"] is False and first["persisted"] is False
    assert first["job"] is None and first["dispatchable"] is False
    assert first["scan_enabled"] is False
    assert session.get(BrandScanTaskModel, task_id).status == task_before == "staged"
    assert session.get(BrandListingSnapshotModel, snapshot_id).content_sha256 == snapshot_before
    assert session.scalar(select(func.count()).select_from(BrandListingImageModel)) == image_count
    assert not {"images", "captured_at", "source_url", "raw_url", "title"} & first.keys()


@pytest.mark.parametrize("bad", [
    {"images": []},
    {"images": [dict(sequence_index=0, asset_sha256="b" * 64,
                      source_url_sha256="c" * 64, role="hero", rights_state="verified")]},
    {"images": [dict(sequence_index=2, asset_sha256="b" * 64,
                      source_url_sha256="c" * 64, role="hero")]},
    {"images": [dict(sequence_index=0, asset_sha256="Z" * 64,
                      source_url_sha256="c" * 64, role="hero")]},
    {"page_count": 6},
    {"page_count": 0},
    {"page_count": True},
    {"captured_at": datetime(2026, 10, 18, 10, 0)},
    {"listing_key": "https://www.etsy.com/shop/Hats"},
    {"observed_revision": "x" * 129},
    {"images": [dict(sequence_index=i, asset_sha256="a" * 64,
                      source_url_sha256="b" * 64, role="unknown")
                 for i in range(21)]},
])
def test_bad_manifest_is_rejected(bad):
    with pytest.raises(ValidationError):
        manifest(**bad)


def test_extraneous_fields_and_forged_verification_rejected():
    with pytest.raises(ValidationError):
        ResultManifest.model_validate({
            **manifest().model_dump(),
            "source_url": "https://evil.example/harvest",
        })
    with pytest.raises(ValidationError):
        ResultManifest.model_validate({
            **manifest().model_dump(),
            "verified": True,
        })
    with pytest.raises(ValidationError):
        PreviewScanResult.model_validate({
            "task_id": "12345678-1111-4111-8111-123456789012",
            "manifest": manifest().model_dump(),
            "lease_id": "12345678-2222-4222-8222-123456789012",
        })


def test_digest_binds_tenant_shop_task_and_snapshot(session):
    shop, snapshot_id, task_id = make_task(session)
    item = manifest()
    current = preview_scan_result(shop["id"],
        PreviewScanResult(task_id=UUID(task_id), manifest=item),
        Response(),session,principal())
    unchanged = item.model_copy(update={"captured_at": item.captured_at.astimezone(
        timezone(timedelta(hours=7)))})
    assert manifest_digest(tenant_id="tenant-a", shop_id=shop["id"], task_id=task_id,
        snapshot_id=snapshot_id,
        snapshot_sha256=session.get(BrandListingSnapshotModel,snapshot_id).content_sha256,
        manifest=ResultManifest.model_validate(unchanged.model_dump())) == current["manifest_sha256"]
    altered = item.model_copy(deep=True)
    altered.images[0].asset_sha256 = "e" * 64
    modified = preview_scan_result(shop["id"],
        PreviewScanResult(task_id=UUID(task_id), manifest=altered),
        Response(),session,principal())
    assert current["manifest_sha256"] != modified["manifest_sha256"]


def test_task_shop_tenant_status_and_listing_identity_guards(session):
    first, _, task_a = make_task(session)
    second, _, task_b = make_task(session, tenant="tenant-b", name="Different")
    same_tenant, _, task_c = make_task(session, name="Sibling")
    packet = PreviewScanResult(task_id=UUID(task_a), manifest=manifest())
    for shop_id, who in [(first["id"], "tenant-b"), (second["id"],"tenant-a")]:
        with pytest.raises(HTTPException) as err:
            preview_scan_result(shop_id, packet, Response(), session, principal(who))
        assert err.value.status_code == 404
    for shop_id, task in [(first["id"],task_b),(same_tenant["id"],task_a)]:
        with pytest.raises(HTTPException) as err:
            preview_scan_result(shop_id,
                PreviewScanResult(task_id=UUID(task),manifest=manifest()),
                Response(),session,principal())
        assert err.value.status_code == 404
    with pytest.raises(HTTPException) as wrong_listing:
        preview_scan_result(first["id"],
            PreviewScanResult(task_id=UUID(task_a),
                manifest=manifest(listing_key="Something_else")),
            Response(),session,principal())
    assert wrong_listing.value.status_code == 409
    task = session.get(BrandScanTaskModel, task_a)
    task.status = "cancelled"
    session.commit()
    with pytest.raises(HTTPException) as cancelled:
        preview_scan_result(first["id"],packet,Response(),session,principal())
    assert cancelled.value.status_code == 409


def test_policy_and_capture_window_fail_closed(session):
    shop, _, task_id = make_task(session)
    packet = PreviewScanResult(task_id=UUID(task_id), manifest=manifest())
    row = session.get(BrandShopModel, shop["id"])
    row.access_mode = "public_observation"
    session.commit()
    with pytest.raises(HTTPException) as declined:
        preview_scan_result(shop["id"],packet,Response(),session,principal())
    assert declined.value.status_code == 409
    row.access_mode = "authorized"
    session.commit()
    for time in [now_utc()-timedelta(days=31), now_utc()+timedelta(minutes=7)]:
        with pytest.raises(HTTPException) as stale:
            preview_scan_result(shop["id"],
                PreviewScanResult(task_id=UUID(task_id),
                                  manifest=manifest(captured_at=time)),
                Response(),session,principal())
        assert stale.value.status_code == 422
    assert valid_capture_window(now_utc(),now=now_utc())




def test_http_result_preview_requires_tenant_bound_staged_task_and_safe_body():
    """HTTP/TestClient ensures JSON parser, dependency and 422 behavior match contract."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from app.core.database import get_db
    from app.modules.branding_dna.scan_result_router import router, CONFIGURE

    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandListingSnapshotModel.__table__,
        BrandListingImageModel.__table__, BrandScanTaskModel.__table__,
    ])
    try:
        with Session(engine) as db:
            shop, _, task_id = make_task(db)
        app = FastAPI()
        app.include_router(router)
        current_tenant = {"tenant": "tenant-a"}

        def principal_override():
            return principal(current_tenant["tenant"])

        def db_override():
            with Session(engine) as db:
                yield db

        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[CONFIGURE] = principal_override
        with TestClient(app) as client:
            endpoint = f"/api/v1/branding-dna/shops/{shop['id']}/scan-results/preview"
            payload = {
                "task_id": task_id,
                "manifest": manifest().model_dump(mode="json"),
            }
            ok = client.post(endpoint, json=payload)
            assert ok.status_code == 200, ok.text
            assert ok.headers["cache-control"] == "no-store"
            assert ok.json()["persisted"] is False
            assert ok.json()["scan_enabled"] is False
            assert ok.json()["marketplace_authorization_verified"] is False

            invalid = client.post(endpoint, json={
                **payload,
                "manifest": {
                    **payload["manifest"],
                    "images": [{**payload["manifest"]["images"][0],
                                "rights_state": "verified"}],
                },
            })
            assert invalid.status_code == 422
            smuggling = client.post(endpoint, json={
                **payload, "shell_command": "fetch other tenant images",
            })
            assert smuggling.status_code == 422
            current_tenant["tenant"] = "tenant-b"
            foreign = client.post(endpoint, json=payload)
            assert foreign.status_code == 404
            with Session(engine) as db:
                assert db.get(BrandScanTaskModel, task_id).status == "staged"
    finally:
        engine.dispose()

def test_manifest_digest_not_an_attestation():
    item=manifest()
    base=dict(tenant_id="a",shop_id="b",task_id="c",
              snapshot_id="d",snapshot_sha256="a" * 64,manifest=item)
    initial=manifest_digest(**base)
    assert initial != manifest_digest(**{**base,"tenant_id":"tenant-other"})
    assert initial != manifest_digest(**{**base,"snapshot_sha256":"b"*64})
    assert initial != manifest_digest(**{**base,"task_id":"other"})
