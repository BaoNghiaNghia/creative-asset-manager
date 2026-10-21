"""Access declarations preserve rights gates and tenant isolation."""
from types import SimpleNamespace
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import pytest

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.access_declaration_model import BrandShopAccessDeclarationModel
from app.modules.branding_dna.access_review_model import BrandShopAccessReviewModel
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel
from app.modules.branding_dna.access_declaration_router import read_shop_scan_readiness
from app.modules.branding_dna.access_declaration_router import (
    ShopRelationshipDeclaration, declare_shop_relationship, latest_access_declarations,
    OwnedShopDeclarationItem, BulkOwnedShopDeclaration, declare_owned_shops_bulk,
)
from app.modules.branding_dna.router import add_shop, ShopCreate


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandShopAccessDeclarationModel.__table__,
        BrandShopAccessReviewModel.__table__,
        BrandScanTaskModel.__table__, BrandListingSnapshotModel.__table__,
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


def test_admin_readiness_is_tenant_scoped_and_never_dispatchable(db):
    alpha = register(db, "tenant-a", "OwnedHat")
    competitor = register(db, "tenant-a", "CompetitorHat")
    other = register(db, "tenant-b", "OtherHat")
    declare(db, alpha["id"], "owned", "written-consent-doc-001")
    declare(db, competitor["id"], "competitor_research")
    declare(db, other["id"], "authorized", "agreement-file-2026-11", "tenant-b")
    from uuid import uuid4
    task = BrandScanTaskModel(
        tenant_id="tenant-a", shop_id=alpha["id"],
        snapshot_id=str(uuid4()), created_by="admin-1", status="staged",
    )
    db.add(task)
    db.commit()
    original = (task.status, task.lease_id, task.agent_id)
    response = Response()
    result = read_shop_scan_readiness(response, session=db, principal=user("tenant-a"))
    assert response.headers["Cache-Control"] == "no-store"
    assert result["scan_enabled"] is False
    assert result["ready_count"] == 0
    assert result["blocked_count"] == 2
    assert result["staged_tasks"] == 1
    items = {item["shop_id"]: item for item in result["items"]}
    assert other["id"] not in items
    assert "shop_access_pending_verification" in items[alpha["id"]]["blockers"]
    assert "shop_competitor_research_only" in items[competitor["id"]]["blockers"]
    assert "shop_access_not_declared" not in items[alpha["id"]]["blockers"]
    assert "marketplace_authorization_not_verified" in items[alpha["id"]]["blockers"]
    assert "scan_task_dispatch_not_deployed" in items[competitor["id"]]["blockers"]
    assert "evidence_reference" not in str(result)
    assert db.get(BrandShopModel, alpha["id"]).status == "draft"
    db.refresh(task)
    assert (task.status, task.lease_id, task.agent_id) == original
    other_result = read_shop_scan_readiness(Response(), session=db, principal=user("tenant-b"))
    assert other_result["shop_count"] == 1
    assert other_result["items"][0]["shop_id"] == other["id"]


def test_latest_declaration_is_stable_and_reflected_in_readiness(db):
    shop = register(db, name="ChangingSource")
    declare(db, shop["id"], "owned", "first-document-file")
    declare(db, shop["id"], "competitor_research")
    result = read_shop_scan_readiness(Response(), session=db, principal=user())
    assert result["items"][0]["declaration_relationship"] == "competitor_research"
    assert "shop_competitor_research_only" in result["items"][0]["blockers"]
    declarations = latest_access_declarations(Response(), session=db, principal=user())
    assert declarations["items"][0]["relationship"] == "competitor_research"


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


def test_bulk_owned_multiple_amazon_and_etsy_no_automatic_dispatch(db):
    from pydantic import ValidationError
    shops = [
        add_shop(ShopCreate(platform="amazon", region="US",
                           storefront_url="https://www.amazon.com/stores/CompanyAlpha"),
                 session=db, principal=user()),
        add_shop(ShopCreate(platform="amazon", region="US",
                           storefront_url="https://www.amazon.com/stores/CompanyBeta"),
                 session=db, principal=user()),
        add_shop(ShopCreate(platform="amazon", region="GB",
                           storefront_url="https://www.amazon.co.uk/stores/CompanyAlpha"),
                 session=db, principal=user()),
        register(db, name="CompanyEtsy"),
    ]
    body = BulkOwnedShopDeclaration(
        acknowledge_marketplace_authorization_not_verified=True,
        items=[OwnedShopDeclarationItem(
            shop_id=s["id"], evidence_reference=f"company-contract-{i:02d}"
        ) for i, s in enumerate(shops)],
    )
    response = Response()
    created = declare_owned_shops_bulk(body, response, session=db, principal=user())
    assert response.status_code == 201
    assert created["created"] == 4 and created["replayed"] == 0
    assert created["rights_verified"] is False
    assert created["scan_enabled"] is False and created["dispatchable"] is False
    assert len({s["declaration_id"] for s in created["items"]}) == 4
    for shop in shops:
        row = db.get(BrandShopModel, shop["id"])
        assert row.status == "draft" and row.access_mode == "public_observation"
    readiness = read_shop_scan_readiness(Response(), session=db, principal=user())
    assert readiness["blocked_count"] == 4 and readiness["ready_count"] == 0
    for shop in readiness["items"]:
        assert shop["declaration_relationship"] == "owned"
        assert "shop_access_pending_verification" in shop["blockers"]
        assert "marketplace_authorization_not_verified" in shop["blockers"]
    retry_resp = Response()
    replay = declare_owned_shops_bulk(body, retry_resp, session=db, principal=user())
    assert retry_resp.status_code == 200
    assert replay["created"] == 0 and replay["replayed"] == 4
    assert db.query(BrandShopAccessDeclarationModel).count() == 4
    with pytest.raises(ValidationError):
        BulkOwnedShopDeclaration(items=body.items,
            acknowledge_marketplace_authorization_not_verified=False)


def test_bulk_owned_rejects_foreign_missing_duplicate_and_bad_reference_atomically(db):
    from uuid import uuid4
    own = add_shop(ShopCreate(platform="amazon",
                             storefront_url="https://www.amazon.com/stores/CompanyA"),
                   session=db, principal=user())
    foreign = register(db, "tenant-b", "ForeignShop")
    good = (own["id"], "company-evidence-2026")
    def make(rows):
        return BulkOwnedShopDeclaration(
            acknowledge_marketplace_authorization_not_verified=True,
            items=[OwnedShopDeclarationItem(shop_id=sid,evidence_reference=ref)
                   for sid,ref in rows],
        )
    for batch, expected in (
        ([good,(foreign["id"],"foreign-evidence")],404),
        ([good,(str(uuid4()),"missing-evidence")],404),
        ([good,good],422),
        ([good,(foreign["id"],"line\nbreak")],422),
    ):
        with pytest.raises(HTTPException) as exc:
            declare_owned_shops_bulk(make(batch),Response(),session=db,principal=user())
        assert exc.value.status_code == expected
        assert db.query(BrandShopAccessDeclarationModel).count() == 0
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        OwnedShopDeclarationItem(shop_id=own["id"],evidence_reference="short")


def test_bulk_owned_updates_only_explicit_shop_attestations(db):
    first = register(db,name="OwnedA")
    second = register(db,name="OwnedB")
    untouched = register(db,name="UnusedC")
    def make(rows):
        return BulkOwnedShopDeclaration(
            acknowledge_marketplace_authorization_not_verified=True,
            items=[OwnedShopDeclarationItem(shop_id=sid,evidence_reference=ref)
                   for sid,ref in rows],
        )
    declare_owned_shops_bulk(make([(first["id"],"initial-proof")]),
                             Response(),session=db,principal=user())
    result = declare_owned_shops_bulk(make([
        (first["id"],"updated-proof"),(second["id"],"second-proof"),
    ]),Response(),session=db,principal=user())
    assert result["created"] == 2
    assert db.query(BrandShopAccessDeclarationModel).count() == 3
    by_shop = {x["shop_id"]:x for x in latest_access_declarations(
        Response(),session=db,principal=user())["items"]}
    assert by_shop[first["id"]]["evidence_reference"] == "updated-proof"
    assert by_shop[second["id"]]["evidence_reference"] == "second-proof"
    assert untouched["id"] not in by_shop