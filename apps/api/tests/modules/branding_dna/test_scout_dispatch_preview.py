"""Paired Scout dispatch-preview stays tenant-isolated and non-executable."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.scout_session_router import dispatch_preview
from app.modules.realistic_review_ugc.model import RrugcScoutAgentModel
from app.modules.realistic_review_ugc.service import token_digest
from app.modules.branding_dna.router import ShopCreate, add_shop

TOKEN = "test-tenant-isolated-scout-token-with-sufficient-length"


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandListingSnapshotModel.__table__,
        BrandScanTaskModel.__table__, RrugcScoutAgentModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def principal(tenant):
    return SimpleNamespace(active_tenant_id=tenant, actor_id="admin")


def enroll(db, tenant):
    row = RrugcScoutAgentModel(
        tenant_id=tenant, name="Scout", active=True, status="offline",
        token_hash=token_digest(TOKEN), created_by_user_id="admin",
    )
    db.add(row)
    db.commit()
    return row


def test_preview_lists_blockers_without_dispatching_or_leaking_shops(db):
    agent_a = enroll(db, "tenant-a")
    agent_b = enroll(db, "tenant-b")
    a = add_shop(ShopCreate(
        platform="amazon", storefront_url="https://www.amazon.com/stores/AlphaHats",
    ), session=db, principal=principal("tenant-a"))
    add_shop(ShopCreate(
        platform="etsy", storefront_url="https://www.etsy.com/shop/BetaHats",
    ), session=db, principal=principal("tenant-b"))
    task = BrandScanTaskModel(
        tenant_id="tenant-a", shop_id=a["id"], snapshot_id=str(uuid4()),
        created_by="admin", status="staged",
    )
    db.add(task)
    db.commit()
    original_state = (task.status, task.lease_id, task.agent_id, task.lease_generation)

    response = Response()
    result = dispatch_preview(
        agent_a.id, response, authorization="Bearer " + TOKEN, session=db)
    assert response.headers["Cache-Control"] == "no-store"
    assert result["version"] == 1
    assert result["scan_enabled"] is False
    assert result["dispatch"]["active_scan_jobs"] == 0
    assert result["ready_count"] == 0
    assert result["blocked_count"] == 1
    assert result["shop_count"] == 1
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["platform"] == "amazon"
    assert item["staged_tasks"] == 1
    assert item["status"] == "blocked"
    assert "scan_task_dispatch_not_deployed" in item["blockers"]
    assert "marketplace_authorization_not_verified" in item["blockers"]
    assert "shop_access_not_declared" in item["blockers"]
    assert "storefront_url" not in item
    assert "tenant_id" not in item
    assert not any("etsy" in str(x).lower() for x in result["items"])
    db.refresh(task)
    assert (task.status, task.lease_id, task.agent_id, task.lease_generation) == original_state

    other = dispatch_preview(
        agent_b.id, Response(), authorization="Bearer " + TOKEN, session=db)
    assert other["shop_count"] == 1
    assert other["items"][0]["platform"] == "etsy"
    assert other["items"][0]["staged_tasks"] == 0
    assert "etsy_written_permission_not_verified" in other["items"][0]["blockers"]


def test_preview_requires_active_pairing_and_never_returns_lease(db):
    agent = enroll(db, "tenant-a")
    for header in [None, "Bearer invalid-token", "Basic nope"]:
        with pytest.raises(HTTPException) as err:
            dispatch_preview(agent.id, Response(), authorization=header, session=db)
        assert err.value.status_code == 401

    empty = dispatch_preview(agent.id, Response(),
                             authorization="Bearer " + TOKEN, session=db)
    assert empty["items"] == []
    assert empty["ready_count"] == 0
    assert "job" not in empty
    assert "lease" not in empty
    agent.active = False
    db.commit()
    with pytest.raises(HTTPException) as err:
        dispatch_preview(agent.id, Response(),
                         authorization="Bearer " + TOKEN, session=db)
    assert err.value.status_code == 401
