"""Shop registry management does not depend on CAM ownership declarations."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.router import (
    ShopCreate, ShopLifecycleChange, add_shop, update_shop_lifecycle,
)


@pytest.fixture
def db():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandScanTaskModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def actor(tenant="tenant-a"):
    return SimpleNamespace(actor_id="operator", active_tenant_id=tenant)


def create(db, *, tenant="tenant-a", name="ExampleCaps", mode="public_observation"):
    return add_shop(
        ShopCreate(platform="etsy", storefront_url=f"https://www.etsy.com/shop/{name}",
                   access_mode=mode),
        session=db,principal=actor(tenant),
    )["id"]


def lifecycle(db, sid, status="active", tenant="tenant-a"):
    return update_shop_lifecycle(sid,ShopLifecycleChange(status=status),session=db,principal=actor(tenant))


@pytest.mark.parametrize("mode", ["public_observation", "owned", "authorized"])
def test_activation_needs_no_cam_ownership_claim_or_documentary_review(db, mode):
    sid=create(db, mode=mode)
    result=lifecycle(db,sid)
    assert result["item"]["status"]=="active"
    assert result["item"]["access_mode"]==mode
    assert result["rights_verified"] is False
    assert result["scan_enabled"] is False
    assert result["dispatchable"] is False
    assert result["jobs_created"]==0
    assert db.query(BrandScanTaskModel).count()==0
    assert lifecycle(db,sid)["changed"] is False


def test_registry_pause_and_resume_do_not_change_platform_permission(db):
    sid=create(db)
    paused=lifecycle(db,sid,"paused")
    assert paused["item"]["status"]=="paused"
    resumed=lifecycle(db,sid,"active")
    assert resumed["item"]["status"]=="active"
    assert resumed["rights_verified"] is False
    assert db.query(BrandScanTaskModel).count()==0


def test_shop_lifecycle_is_tenant_scoped(db):
    sid=create(db)
    with pytest.raises(HTTPException) as exc:
        lifecycle(db,sid,"active","tenant-b")
    assert exc.value.status_code==404
    assert db.get(BrandShopModel,sid).status=="draft"


def test_legacy_review_fields_do_not_grant_any_rights(db):
    sid=create(db)
    old=ShopLifecycleChange(
        status="active",declaration_id="11111111-1111-4111-8111-111111111111",
        acknowledge_marketplace_rights_not_verified=True,
    )
    result=update_shop_lifecycle(sid,old,session=db,principal=actor())
    assert result["item"]["access_mode"]=="public_observation"
    assert result["scan_enabled"] is False
    assert result["jobs_created"]==0