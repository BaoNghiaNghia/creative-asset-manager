"""Access declarations preserve rights gates and tenant isolation."""
from types import SimpleNamespace
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import pytest

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.access_declaration_model import BrandShopAccessDeclarationModel
from app.modules.branding_dna.access_declaration_router import (
    ShopRelationshipDeclaration, declare_shop_relationship, latest_access_declarations,
)
from app.modules.branding_dna.router import add_shop, ShopCreate


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandShopAccessDeclarationModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def user(tenant="tenant-a"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id="admin-1")


def register(db, tenant="tenant-a", name="OneCap"):
    return add_shop(ShopCreate(platform="etsy",
             storefront_url=f"https://www.etsy.com/shop/{name}"),
             session=db, principal=user(tenant))


def declare(db, shop_id, relationship, reference="", tenant="tenant-a"):
    return declare_shop_relationship(
        shop_id, ShopRelationshipDeclaration(
            relationship=relationship, evidence_reference=reference),
        Response(), session=db, principal=user(tenant))


def test_declaration_does_not_grant_any_scan_permission(db):
    shop = register(db)
    first = declare(db, shop["id"], "owned", "Company contract REF-2026-10")
    assert first["status"] == "pending_review"
    assert first["rights_verified"] is False
    assert first["dispatchable"] is False
    row = db.get(BrandShopModel, shop["id"])
    assert row.status == "draft"
    assert row.access_mode == "public_observation"
    assert db.query(BrandShopAccessDeclarationModel).count() == 1
    second = declare(db, shop["id"], "owned", "Company contract REF-2026-10")
    assert second["replayed"] is True
    assert db.query(BrandShopAccessDeclarationModel).count() == 1
    assert second["id"] == first["id"]


def test_competitor_research_is_not_a_permission_grant(db):
    shop = register(db)
    entry = declare(db, shop["id"], "competitor_research")
    assert entry["status"] == "recorded_only"
    assert entry["rights_verified"] is False
    assert db.get(BrandShopModel, shop["id"]).access_mode == "public_observation"
    newer = declare(db, shop["id"], "authorized", "Permission file REF-99")
    assert newer["status"] == "pending_review"
    assert newer["id"] != entry["id"]
    assert db.get(BrandShopModel, shop["id"]).status == "draft"


def test_cross_tenant_scoping_and_no_leakage(db):
    a = register(db, "tenant-a", "TenantAHat")
    b = register(db, "tenant-b", "TenantBHat")
    declare(db, a["id"], "competitor_research")
    declare(db, b["id"], "owned", "Internal permission DOC-101", "tenant-b")
    with pytest.raises(HTTPException) as exc:
        declare(db, b["id"], "authorized", "Internal permission DOC-101")
    assert exc.value.status_code == 404
    alice = latest_access_declarations(Response(), session=db, principal=user())
    assert len(alice["items"]) == 1
    assert alice["items"][0]["shop_id"] == a["id"]
    assert alice["scan_enabled"] is False
    assert all(row["shop_id"] != b["id"] for row in alice["items"])


@pytest.mark.parametrize("relationship,ref", [
    ("owned", ""), ("authorized", "123"),
])
def test_evidence_required_for_owned_or_authorized(db, relationship, ref):
    shop = register(db)
    with pytest.raises(HTTPException) as exc:
        declare(db, shop["id"], relationship, ref)
    assert exc.value.status_code == 422
    assert db.query(BrandShopAccessDeclarationModel).count() == 0


def test_malformed_input_and_extra_fields_rejected(db):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ShopRelationshipDeclaration(relationship="approved")
    with pytest.raises(ValidationError):
        ShopRelationshipDeclaration(relationship="owned", evidence_reference="x", verified=True)
    shop = register(db)
    with pytest.raises(HTTPException) as exc:
        declare(db, shop["id"], "unknown", "\nInvalid")
    assert exc.value.status_code == 422
