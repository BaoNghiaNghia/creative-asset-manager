"""BD-02 operator readiness is read-only, tenant-scoped and fail-closed."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.evidence_model import BrandListingImageModel, BrandListingSnapshotModel
from app.modules.branding_dna.evidence_router import GalleryImport, ListingImport, import_listing
from app.modules.branding_dna.model import BrandShopModel, now_utc
from app.modules.branding_dna.router import ShopCreate, add_shop
from app.modules.branding_dna.scout_session_model import BrandScoutSessionModel
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.task_router import TaskStage, scan_readiness, stage_task
from app.modules.realistic_review_ugc.model import RrugcScoutAgentModel

def who(tenant="tenant-a"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id="operator")


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandListingSnapshotModel.__table__,
        BrandListingImageModel.__table__, BrandScanTaskModel.__table__,
        RrugcScoutAgentModel.__table__, BrandScoutSessionModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def make_shop(db, tenant="tenant-a", name="ScanShop", platform="etsy", access="authorized"):
    url = (f"https://www.etsy.com/shop/{name}" if platform == "etsy" else
           f"https://www.amazon.com/stores/{name}")
    return add_shop(ShopCreate(
        platform=platform, storefront_url=url, access_mode=access,
    ), session=db, principal=who(tenant))["id"]


def make_observation(db, shop_id, tenant="tenant-a"):
    imported = import_listing(shop_id, ListingImport(
        listing_key="item-1", source_revision="r1", export_reference="export-1",
        title="One Product", category="hat", captured_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
        images=[GalleryImport(sequence_index=0, asset_sha256="a"*64, source_url_sha256="b"*64)],
    ), session=db, principal=who(tenant))
    return imported["item"]["id"]


def test_readiness_reports_real_data_but_does_not_dispatch_or_verify(db):
    shop_id = make_shop(db)
    snapshot_id = make_observation(db, shop_id)
    staged = stage_task(shop_id, TaskStage(snapshot_id=snapshot_id), db, who())["item"]
    scout = RrugcScoutAgentModel(
        tenant_id="tenant-a", name="Connected Scout", active=True,
        status="offline", token_hash="a"*64, created_by_user_id="operator",
    )
    db.add(scout)
    db.flush()
    db.add(BrandScoutSessionModel(
        agent_id=scout.id, tenant_id="tenant-a", version="0.2.5",
        shop_count=1, last_seen_at=now_utc(),
    ))
    db.commit()
    before = (db.get(BrandScanTaskModel, staged["id"]).status,
              db.get(BrandListingSnapshotModel, snapshot_id).evidence_state)
    response = Response()
    state = scan_readiness(shop_id, response, db, who())
    assert response.headers["cache-control"] == "no-store"
    assert state["paired_agents_online"] == 1
    assert state["task_counts"]["staged"] == 1
    assert state["listing_snapshot_count"] == 1
    assert state["verified_listing_count"] == 0
    assert state["scan_enabled"] is False
    assert state["dispatchable"] is False
    assert state["active_scan_jobs"] == 0
    assert state["marketplace_authorization_verified"] is False
    assert "etsy_written_permission_not_verified" in state["blockers"]
    assert "scan_task_dispatch_not_deployed" in state["blockers"]
    assert before == (db.get(BrandScanTaskModel, staged["id"]).status,
                      db.get(BrandListingSnapshotModel, snapshot_id).evidence_state)


def test_readiness_does_not_leak_tenant_data(db):
    shop_a = make_shop(db)
    shop_b = make_shop(db, tenant="tenant-b", name="OtherShop")
    make_observation(db, shop_a)
    make_observation(db, shop_b, tenant="tenant-b")
    with pytest.raises(HTTPException) as denied:
        scan_readiness(shop_a, Response(), db, who("tenant-b"))
    assert denied.value.status_code == 404
    own = scan_readiness(shop_b, Response(), db, who("tenant-b"))
    assert own["listing_snapshot_count"] == 1
    assert own["paired_agents_online"] == 0
    assert "paired_agent_not_online" in own["blockers"]


def test_unapproved_shop_and_old_heartbeat_are_blocked(db):
    shop = make_shop(db, platform="amazon", access="public_observation")
    scout = RrugcScoutAgentModel(
        tenant_id="tenant-a", name="Old Scout", active=True,
        status="offline", token_hash="a"*64, created_by_user_id="operator",
    )
    db.add(scout)
    db.flush()
    db.add(BrandScoutSessionModel(
        agent_id=scout.id, tenant_id="tenant-a", version="0.2.5", shop_count=1,
        last_seen_at=now_utc() - timedelta(minutes=4),
    ))
    db.commit()
    state = scan_readiness(shop, Response(), db, who())
    assert state["paired_agents_online"] == 0
    assert "shop_access_not_declared" in state["blockers"]
    assert "amazon_agent_policy_review_not_verified" in state["blockers"]
    assert "paired_agent_not_online" in state["blockers"]
