"""Conservative, read-only URL scope tests for a future authorized adapter."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.evidence_model import BrandListingImageModel, BrandListingSnapshotModel
from app.modules.branding_dna.evidence_router import GalleryImport, ListingImport, import_listing
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.router import ShopCreate, add_shop
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.task_router import TaskStage, stage_task
from app.modules.branding_dna.scan_scope_router import PreviewScanScope, preview_scan_scope
from app.modules.branding_dna.scan_scope_service import (
    evaluate_storefront_url, inactive_scan_budget,
)


def principal(tenant="tenant-a"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id="scope-operator")


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandListingSnapshotModel.__table__,
        BrandListingImageModel.__table__, BrandScanTaskModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def make_task(db, tenant="tenant-a", platform="etsy", shop="ScopeHats", access="authorized"):
    url = (f"https://www.etsy.com/shop/{shop}" if platform == "etsy"
           else f"https://www.amazon.com/stores/{shop}")
    stored = add_shop(
        ShopCreate(platform=platform, storefront_url=url, access_mode=access),
        session=db, principal=principal(tenant),
    )
    evidence = import_listing(
        stored["id"],
        ListingImport(
            listing_key="Listing_0001", source_revision="rev-1",
            export_reference="authorized-export-1",
            title="Original source hat", category="hats",
            captured_at=datetime(2026,10,18,tzinfo=timezone.utc),
            images=[GalleryImport(
                sequence_index=0, asset_sha256="a" * 64,
                source_url_sha256="b" * 64,
            )],
        ),
        session=db, principal=principal(tenant),
    )
    job = stage_task(
        stored["id"], TaskStage(snapshot_id=evidence["item"]["id"]),
        session=db, principal=principal(tenant),
    )
    return stored, evidence["item"]["id"], job["item"]["id"]


@pytest.mark.parametrize("platform,stored,candidate", [
    ("etsy", "https://www.etsy.com/shop/TestHats", "https://www.etsy.com/shop/TestHats"),
    ("etsy", "https://www.etsy.com/shop/TestHats", "https://www.etsy.com/shop/TestHats/items"),
    ("etsy", "https://www.etsy.com/shop/TestHats", "https://www.etsy.com/shop/TestHats/items/saved"),
    ("amazon", "https://www.amazon.com/stores/EMBROLY/page/ABC-1",
     "https://www.amazon.com/stores/EMBROLY/page/ABC-1"),
    ("amazon", "https://www.amazon.com/stores/EMBROLY/page/ABC-1",
     "https://www.amazon.com/stores/EMBROLY/page/ABC-1/items"),
    ("amazon", "https://www.amazon.co.uk/stores/EMBROLY",
     "https://www.amazon.co.uk/stores/EMBROLY/listing/A1"),
])
def test_within_registered_storefront_not_authorization(platform,stored,candidate):
    assert evaluate_storefront_url(
        platform=platform, registered_url=stored, candidate_url=candidate,
    ) == (True, "registered_storefront_subpath")


@pytest.mark.parametrize("candidate", [
    "https://www.etsy.com/shop/TestHatsOthers",
    "https://www.etsy.com/shop/Other",
    "https://etsy.com/shop/TestHats",
    "https://www.etsy.com.evil.example/shop/TestHats",
    "http://www.etsy.com/shop/TestHats",
    "https://www.etsy.com:443/shop/TestHats",
    "https://user:pass@www.etsy.com/shop/TestHats",
    "https://www.etsy.com@evil.example/shop/TestHats",
    "https://www.etsy.com/shop/TestHats?sort=recent",
    "https://www.etsy.com/shop/TestHats#",
    "https://www.etsy.com/shop/TestHats/%2e%2e",
    "https://www.etsy.com/shop/TestHats/%252f",
    "https://www.etsy.com/shop/TestHats/../Other",
    "https://www.etsy.com/shop/TestHats/./items",
    "https://www.etsy.com/shop/TestHats//items",
    "https://www.etsy.com/shop/TestHats/one/two/three",
    "https://www.etsy.com/shop/TestHats\\@evil.test",
    "https://www.etsy.com/shop/TestHats\nX-Injected: hi",
    "https://www.etsy.com/shop/TestHats;drop",
    "https://ｗｗｗ.etsy.com/shop/TestHats",
    "file:///etc/passwd",
    "http://127.0.0.1/",
    "https://169.254.169.254/latest/meta-data",
])
def test_attacker_controlled_url_rejected(candidate):
    accepted, _ = evaluate_storefront_url(
        platform="etsy", registered_url="https://www.etsy.com/shop/TestHats",
        candidate_url=candidate,
    )
    assert accepted is False


def test_mismatched_amazon_page_and_unsupported_marketplace_fail_closed():
    root="https://www.amazon.com/stores/EMBROLY/page/ABC-1"
    for url in [
        "https://www.amazon.com/stores/EMBROLY/page/DIFFERENT",
        "https://www.amazon.com/dp/1234567890",
        "https://www.amazon.ca/stores/EMBROLY/page/ABC-1",
        "https://www.amazon.com/stores/EMBROLY/page/ABC-1/one/two/three",
    ]:
        assert not evaluate_storefront_url(
            platform="amazon", registered_url=root, candidate_url=url,
        )[0]
    assert not evaluate_storefront_url(
        platform="amazon", registered_url="https://amazon.evil.test/stores/EMBROLY",
        candidate_url=root,
    )[0]
    assert not evaluate_storefront_url(
        platform="shopify", registered_url="https://hats.myshopify.com/",
        candidate_url="https://hats.myshopify.com/",
    )[0]
    assert inactive_scan_budget() == {
        "max_pages":5, "max_gallery_items":20,
        "max_navigation_seconds":20, "max_redirects":0,
        "max_extra_path_components":2, "request_allowed":False,
    }


def test_preview_is_bound_to_tenant_shop_staged_task_and_not_executable(db):
    shop, snapshot_id, task_id = make_task(db)
    before = (
        db.get(BrandScanTaskModel,task_id).status,
        db.get(BrandListingSnapshotModel,snapshot_id).evidence_state,
    )
    response=Response()
    packet=PreviewScanScope(
        task_id=UUID(task_id),
        candidate_url=shop["storefront_url"]+"/listing/123",
    )
    result=preview_scan_scope(shop["id"],packet,response,db,principal())
    assert response.headers["cache-control"] == "no-store"
    assert result["candidate_within_storefront"] is True
    assert result["shop_access_declared"] is True
    assert result["job"] is None and result["dispatchable"] is False
    assert result["scan_enabled"] is False
    assert result["marketplace_authorization_verified"] is False
    assert result["limits"]["request_allowed"] is False
    assert set(result["blockers"]) >= {
        "scan_dispatch_disabled","marketplace_authorization_unverified",
        "navigation_not_authorized",
    }
    assert "candidate_url" not in result and "storefront_url" not in result
    assert before == (
        db.get(BrandScanTaskModel,task_id).status,
        db.get(BrandListingSnapshotModel,snapshot_id).evidence_state,
    )
    denied=preview_scan_scope(shop["id"],PreviewScanScope(
        task_id=UUID(task_id),candidate_url="http://127.0.0.1/admin",
    ),Response(),db,principal())
    assert denied["candidate_within_storefront"] is False
    assert denied["job"] is None


def test_preview_denies_cross_tenant_cross_shop_cancelled_and_invalid_body(db):
    first, _, task_a = make_task(db)
    second, _, task_b = make_task(db,tenant="tenant-b",shop="OtherShop")
    same_tenant_shop, _, other_task = make_task(db,shop="Sibling")
    original=PreviewScanScope(task_id=UUID(task_a),candidate_url=first["storefront_url"])
    for shop_id, packet, tenant in [
        (first["id"], original, "tenant-b"),
        (first["id"], PreviewScanScope(task_id=UUID(task_b),
                                      candidate_url=first["storefront_url"]), "tenant-a"),
        (same_tenant_shop["id"], original, "tenant-a"),
    ]:
        with pytest.raises(HTTPException) as err:
            preview_scan_scope(shop_id,packet,Response(),db,principal(tenant))
        assert err.value.status_code==404
    row=db.get(BrandScanTaskModel,task_a)
    row.status="cancelled"
    db.commit()
    with pytest.raises(HTTPException) as err:
        preview_scan_scope(first["id"],original,Response(),db,principal())
    assert err.value.status_code==409
    with pytest.raises(ValidationError):
        PreviewScanScope(
            task_id=UUID(other_task),candidate_url=first["storefront_url"],
            shell_command="curl localhost",
        )


def test_access_declaration_does_not_grant_marketplace_rights(db):
    shop,snapshot_id,task_id=make_task(db)
    stored=db.get(BrandShopModel,shop["id"])
    stored.access_mode="public_observation"
    db.commit()
    packet=PreviewScanScope(task_id=UUID(task_id),candidate_url=shop["storefront_url"])
    output=preview_scan_scope(shop["id"],packet,Response(),db,principal())
    assert output["candidate_within_storefront"]
    assert output["shop_access_declared"] is False
    assert "shop_access_not_declared" in output["blockers"]
    assert not output["dispatchable"] and not output["scan_enabled"]
