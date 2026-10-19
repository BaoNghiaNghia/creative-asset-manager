"""Paired Scout dispatch-preview stays tenant-isolated and non-executable."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.access_declaration_model import BrandShopAccessDeclarationModel
from app.modules.branding_dna.access_review_model import BrandShopAccessReviewModel
from app.modules.branding_dna.access_declaration_router import ShopRelationshipDeclaration, declare_shop_relationship
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.scout_session_router import dispatch_preview
from app.modules.branding_dna.access_declaration_router import read_shop_scan_readiness
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
        BrandShopAccessDeclarationModel.__table__, BrandShopAccessReviewModel.__table__,
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

    # A pending declaration changes the *explanation*, never shop access or dispatch.
    declared = declare_shop_relationship(
        a["id"], ShopRelationshipDeclaration(
            relationship="owned", evidence_reference="internal-authorization-ref-2026"),
        Response(), session=db, principal=principal("tenant-a"),
    )
    assert declared["rights_verified"] is False
    pending = dispatch_preview(
        agent_a.id, Response(), authorization="Bearer " + TOKEN, session=db)
    assert "shop_access_pending_verification" in pending["items"][0]["blockers"]
    assert "shop_access_not_declared" not in pending["items"][0]["blockers"]
    assert "declaration_relationship" not in pending["items"][0]
    assert "evidence_reference" not in str(pending)
    assert pending["ready_count"] == 0
    assert pending["dispatch"]["active_scan_jobs"] == 0
    assert db.get(BrandShopModel, a["id"]).access_mode == "public_observation"
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


def test_admin_queue_health_counts_are_tenant_scoped_and_read_only(db):
    clock = datetime.now(timezone.utc)
    a = add_shop(ShopCreate(
        platform="amazon", storefront_url="https://www.amazon.com/stores/QueueHats",
    ), session=db, principal=principal("tenant-a"))
    b = add_shop(ShopCreate(
        platform="etsy", storefront_url="https://www.etsy.com/shop/OtherQueueHats",
    ), session=db, principal=principal("tenant-b"))
    specs = (
        ("staged", None, None),
        ("pending", None, None),
        ("retry", None, clock - timedelta(seconds=1)),
        ("retry", None, clock + timedelta(hours=1)),
        ("leased", clock - timedelta(seconds=1), None),
        ("leased", clock + timedelta(hours=1), None),
        ("blocked", None, None),
        ("completed", None, None),
    )
    tasks = []
    for status, lease_until, retry_after in specs:
        task = BrandScanTaskModel(
            tenant_id="tenant-a", shop_id=a["id"], snapshot_id=str(uuid4()),
            status=status, created_by="admin", lease_until=lease_until,
            retry_after=retry_after, attempts=1 if status in ("leased", "retry") else 0,
        )
        tasks.append(task)
    tasks.append(BrandScanTaskModel(
        tenant_id="tenant-b", shop_id=b["id"], snapshot_id=str(uuid4()),
        status="leased", created_by="admin", attempts=1,
        lease_until=clock - timedelta(days=10),
    ))
    db.add_all(tasks)
    db.commit()

    # Inspecting a controlled clock allows precise retry/expiry assertions.
    from app.modules.branding_dna.scan_readiness_service import shop_scan_readiness
    items = shop_scan_readiness(db, tenant_id="tenant-a", include_queue=True, now=clock)
    assert len(items) == 1
    assert items[0]["queue"] == {
        "staged": 1, "pending": 1, "leased": 2, "retry": 2,
        "blocked": 1, "completed": 1, "cancelled": 0,
        "expired_leases": 1, "due_retries": 1,
    }
    response = Response()
    overview = read_shop_scan_readiness(response, session=db, principal=principal("tenant-a"))
    assert response.headers["Cache-Control"] == "no-store"
    assert overview["scan_enabled"] is False
    assert overview["ready_count"] == 0
    assert overview["shop_count"] == 1
    assert overview["queue_totals"]["pending"] == 1
    assert overview["queue_totals"]["retry"] == 2
    assert overview["queue_totals"]["leased"] == 2
    assert overview["queue_totals"]["expired_leases"] == 1
    assert overview["queue_totals"]["due_retries"] == 1
    assert overview["staged_tasks"] == 1
    assert "tenant_id" not in overview["items"][0]
    assert "lease_id" not in str(overview)
    assert read_shop_scan_readiness(Response(), session=db, principal=principal("tenant-b"))["queue_totals"]["leased"] == 1
    # Reads must not recover, claim or mutate a task.
    db.expire_all()
    assert [(db.get(BrandScanTaskModel, task.id).status,
             db.get(BrandScanTaskModel, task.id).lease_until,
             db.get(BrandScanTaskModel, task.id).retry_after)
            for task in tasks] == [
        (status, lease_until.replace(tzinfo=None) if lease_until else None,
         retry_after.replace(tzinfo=None) if retry_after else None)
        for status, lease_until, retry_after in specs
    ] + [("leased", (clock - timedelta(days=10)).replace(tzinfo=None), None)]


def test_empty_admin_queue_health_reports_zeros_without_dispatch(db):
    response = Response()
    result = read_shop_scan_readiness(response, session=db, principal=principal("tenant-a"))
    assert result["queue_totals"] == {
        "staged": 0, "pending": 0, "leased": 0, "retry": 0,
        "blocked": 0, "completed": 0, "cancelled": 0,
        "expired_leases": 0, "due_retries": 0,
    }
    assert result["items"] == [] and result["scan_enabled"] is False
