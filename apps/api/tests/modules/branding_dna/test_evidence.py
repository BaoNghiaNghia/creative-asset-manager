"""BD-01: authorized export evidence, provenance, replay and tenant isolation."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.evidence_model import BrandListingImageModel, BrandListingSnapshotModel
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.router import ShopCreate, add_shop
from app.modules.branding_dna.evidence_router import (
    ListingImport, GalleryImport, import_listing, list_listing_evidence, listing_gallery,
)

NOW = datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc)
HASH1 = "a" * 64
HASH2 = "b" * 64


def principal(tenant="tenant-1"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id="curator-1")


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__,
        BrandListingSnapshotModel.__table__,
        BrandListingImageModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def shop(db, tenant="tenant-1", access="authorized", name="AuthorizedHats"):
    return add_shop(ShopCreate(platform="etsy",
                             storefront_url=f"https://www.etsy.com/shop/{name}",
                             access_mode=access), session=db, principal=principal(tenant))


def payload(**overrides):
    content = dict(
        listing_key="listing-1001", source_revision="export-r1",
        export_reference="export-batch-001",
        title="Twill Embroidered Cap", category="hats",
        captured_at=NOW,
        images=[
            GalleryImport(sequence_index=0, asset_sha256=HASH1,
                          source_url_sha256=HASH2, role="hero"),
            GalleryImport(sequence_index=1, asset_sha256=HASH2,
                          source_url_sha256=HASH1, role="detail", variant_key="navy"),
        ],
    )
    content.update(overrides)
    return ListingImport(**content)


def list_items(db, shop_id, tenant="tenant-1", **kwargs):
    return list_listing_evidence(shop_id, session=db, principal=principal(tenant),
                                 limit=kwargs.get("limit", 25), offset=kwargs.get("offset", 0))


def test_authorized_shop_import_and_gallery_order(db):
    s = shop(db)
    result = import_listing(s["id"], payload(), session=db, principal=principal())
    assert not result["replayed"] and result["item"]["image_count"] == 2
    assert result["item"]["evidence_state"] == "imported_unverified"
    assert result["item"]["coverage_state"] == "unknown"
    rows = list_items(db, s["id"])
    assert rows["count"] == 1 and rows["scan_enabled"] is False
    detail = listing_gallery(s["id"], result["item"]["id"], session=db, principal=principal())
    assert [im["sequence_index"] for im in detail["images"]] == [0, 1]
    assert [im["role"] for im in detail["images"]] == ["hero", "detail"]
    assert all(im["media_available"] is False for im in detail["images"])


def test_idempotent_replay_preserves_snapshot_and_images(db):
    s = shop(db)
    first = import_listing(s["id"], payload(), session=db, principal=principal())
    replay = import_listing(s["id"], payload(), session=db, principal=principal())
    assert replay["replayed"] and replay["item"]["id"] == first["item"]["id"]
    assert db.query(BrandListingImageModel).count() == 2
    with pytest.raises(HTTPException) as err:
        import_listing(s["id"], payload(title="Altered title"),
                       session=db, principal=principal())
    assert err.value.status_code == 409
    next_revision = import_listing(
        s["id"], payload(source_revision="export-r2", title="New title"),
        session=db, principal=principal())
    assert next_revision["item"]["id"] != first["item"]["id"]
    assert list_items(db, s["id"])["count"] == 2
    assert db.query(BrandListingImageModel).count() == 4


def test_strict_tenant_and_shop_scoping(db):
    owned = shop(db, tenant="tenant-1")
    other = shop(db, tenant="tenant-2", name="OtherHats")
    first = import_listing(owned["id"], payload(), session=db, principal=principal("tenant-1"))
    assert list_items(db, other["id"], "tenant-2")["count"] == 0
    for action in (
        lambda: list_items(db, owned["id"], "tenant-2"),
        lambda: listing_gallery(owned["id"], first["item"]["id"],
                                session=db, principal=principal("tenant-2")),
        lambda: import_listing(owned["id"], payload(), session=db, principal=principal("tenant-2")),
    ):
        with pytest.raises(HTTPException) as err:
            action()
        assert err.value.status_code == 404
    with pytest.raises(HTTPException) as err:
        listing_gallery(other["id"], first["item"]["id"],
                        session=db, principal=principal("tenant-2"))
    assert err.value.status_code == 404


@pytest.mark.parametrize("mode", ["public_observation"])
def test_public_observation_shop_cannot_import_metadata(db, mode):
    s = shop(db, access=mode)
    with pytest.raises(HTTPException) as err:
        import_listing(s["id"], payload(), session=db, principal=principal())
    assert err.value.status_code == 403
    assert list_items(db, s["id"])["count"] == 0


def test_unknown_media_and_invalid_payload_never_imported(db):
    invalid_images = [
        {"sequence_index": 1, "asset_sha256": HASH1,
         "source_url_sha256": HASH2, "role": "hero"},
        {"sequence_index": 0, "asset_sha256": "not_sha",
         "source_url_sha256": HASH2, "role": "hero"},
    ]
    for im in invalid_images:
        with pytest.raises(ValidationError):
            payload(images=[im])
    for extra in (
        {"source_url": "http://localhost:8000/private"},
        {"tenant_id": "tenant-2"},
        {"media_bytes": "base64..."},
    ):
        with pytest.raises(ValidationError):
            payload(**extra)
    with pytest.raises(ValidationError):
        payload(images=[])
    with pytest.raises(ValidationError):
        payload(images=[{"sequence_index": 0, "asset_sha256": HASH1,
                         "source_url_sha256": HASH2, "rights_state": "permitted"}])
    with pytest.raises(ValidationError, match="timezone"):
        payload(captured_at=datetime(2026, 10, 16, 8, 0))


def test_read_pagination_and_import_metadata_never_implies_scan_complete(db):
    s = shop(db)
    for index in range(3):
        import_listing(s["id"], payload(listing_key=f"listing-{index}"),
                       session=db, principal=principal())
    first_page = list_items(db, s["id"], limit=2, offset=0)
    second_page = list_items(db, s["id"], limit=2, offset=2)
    assert first_page["count"] == second_page["count"] == 3
    assert len(first_page["items"]) == 2 and len(second_page["items"]) == 1
    assert all(x["coverage_state"] == "unknown" for x in first_page["items"])
    assert all(x["source_kind"] == "authorized_export" for x in first_page["items"])


def test_database_scoped_foreign_key_and_uniqueness(db):
    s = shop(db)
    result = import_listing(s["id"], payload(), session=db, principal=principal())
    snapshot = db.scalar(select(BrandListingSnapshotModel).where(
        BrandListingSnapshotModel.id == result["item"]["id"],
    ))
    assert snapshot.tenant_id == "tenant-1"
    assert snapshot.imported_by == "curator-1"
    assert len(snapshot.content_sha256) == 64
