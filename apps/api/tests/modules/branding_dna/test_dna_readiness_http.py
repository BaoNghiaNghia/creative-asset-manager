"""DNA read-only evidence packet is shop-scoped, does not infer or authorize generation."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel, BrandListingImageModel
from app.modules.branding_dna.knowledge_model import BrandKnowledgeDocumentModel, BrandKnowledgeChunkModel
from app.modules.branding_dna.dna_readiness_router import router
from app.modules.branding_dna.dna_draft_router import router as draft_router


def principal(tenant="a", privileged=True):
    return SimpleNamespace(
        active_tenant_id=tenant, actor_id="readiness-owner",
        effective_permissions=frozenset(["ai_provider.configure"] if privileged else ["ai_operations.read"]),
        platform_admin=False,
    )


@pytest.fixture
def test_context():
    engine=create_engine("sqlite+pysqlite:///:memory:",connect_args={"check_same_thread":False},
                         poolclass=StaticPool)
    Base.metadata.create_all(engine,tables=[
        BrandShopModel.__table__,
        BrandListingSnapshotModel.__table__, BrandListingImageModel.__table__,
        BrandKnowledgeDocumentModel.__table__, BrandKnowledgeChunkModel.__table__,
    ])
    session=Session(engine)
    shops=[]
    for tenant in ["a","b"]:
        item=BrandShopModel(tenant_id=tenant,platform="etsy",region="US",
            canonical_shop_key=f"shop-{tenant}",
            storefront_url="https://www.etsy.com/shop/test",display_name=f"Shop {tenant}",
            access_mode="owned",status="draft",created_by="admin")
        session.add(item);session.flush()
        shops.append(item)
    session.commit()
    app=FastAPI();app.include_router(router);app.include_router(draft_router)
    app.dependency_overrides[get_db]=lambda:session
    app.dependency_overrides[require_authenticated_principal]=lambda:principal()
    with TestClient(app) as client:
        yield app,client,shops,session
    session.close();engine.dispose()


def make_doc(session,shop,approved=True):
    d=BrandKnowledgeDocumentModel(tenant_id=shop.tenant_id,shop_id=shop.id,
        filename="tone.md",format="md",category="brand_voice",source_note="Owner",
        content_sha256="a"*64,byte_count=80,text_length=80,chunk_count=1,
        original_file=b"Warm shop voice with calm color and natural fabric choices.",
        status="approved" if approved else "pending_review",uploaded_by="operator")
    session.add(d);session.flush()
    c=BrandKnowledgeChunkModel(tenant_id=shop.tenant_id,shop_id=shop.id,
        document_id=d.id,ordinal=0,content="Warm shop voice with calm color and natural fabric choices.",
        content_sha256="b"*64)
    session.add(c);session.commit()
    return d


def test_no_knowledge_is_not_false_product_evidence(test_context):
    _,client,shops,_=test_context
    res=client.get(f"/api/v1/branding-dna/shops/{shops[0].id}/dna-inputs")
    assert res.status_code==200,res.text
    data=res.json()
    assert data["source_counts"]["approved_knowledge_documents"]==0
    assert "verified_product_evidence_missing" in data["blockers"]
    assert data["dna_draft_ready"] is False
    assert data["dna_generation_enabled"] is False
    assert data["automatic_dna_mutation_enabled"] is False
    assert res.headers["cache-control"]=="no-store"


def test_approved_knowledge_is_cited_but_does_not_override_listing_gate(test_context):
    _,client,shops,session=test_context
    doc=make_doc(session,shops[0],approved=True)
    url=f"/api/v1/branding-dna/shops/{shops[0].id}/dna-inputs"
    a=client.get(url)
    b=client.get(url)
    assert a.status_code==200
    data=a.json()
    assert data["input_fingerprint"]==b.json()["input_fingerprint"]
    assert data["source_counts"]["approved_knowledge_documents"]==1
    assert data["knowledge_sources"][0]["citation"]==f"rag:{doc.id}:0"
    assert data["knowledge_sources"][0]["sha256"]=="a"*64
    assert "Warm shop voice" in data["knowledge_sources"][0]["excerpt"]
    assert data["dna_draft_ready"] is False
    assert not data["dna_generation_enabled"]
    assert data["brand_pick_available"] is False
    assert data["best_seller_verified"] is False


def test_unverified_listing_cannot_claim_dna_ready(test_context):
    _,client,shops,session=test_context
    s=shops[0]
    listing=BrandListingSnapshotModel(
        tenant_id=s.tenant_id,shop_id=s.id,listing_key="listing-001",
        source_revision="1",export_reference="export-1",title="Product hat",
        category="caps",source_kind="authorized_export",
        evidence_state="imported_unverified",coverage_state="unknown",
        captured_at=datetime.now(timezone.utc),imported_by="operator",content_sha256="c"*64,
    )
    session.add(listing);session.commit()
    data=client.get(f"/api/v1/branding-dna/shops/{s.id}/dna-inputs").json()
    assert data["source_counts"]["listing_snapshots"]==1
    assert data["source_counts"]["verified_listing_snapshots"]==0
    assert "imported_listings_are_unverified" in data["notices"]
    assert data["dna_draft_ready"] is False


def test_pending_never_exposed_across_shop_or_tenant(test_context):
    app,client,shops,session=test_context
    make_doc(session,shops[0],approved=False)
    data=client.get(f"/api/v1/branding-dna/shops/{shops[0].id}/dna-inputs").json()
    assert not data["knowledge_sources"]
    assert data["source_counts"]["pending_knowledge_documents"]==1
    app.dependency_overrides[require_authenticated_principal]=lambda:principal("b")
    assert client.get(f"/api/v1/branding-dna/shops/{shops[0].id}/dna-inputs").status_code==404
    assert client.get(f"/api/v1/branding-dna/shops/{shops[1].id}/dna-inputs").json()["knowledge_sources"]==[]


def test_viewer_and_anonymous_cannot_read_private_rag_preview(test_context):
    app,client,shops,_=test_context
    app.dependency_overrides[require_authenticated_principal]=lambda:principal(privileged=False)
    assert client.get(f"/api/v1/branding-dna/shops/{shops[0].id}/dna-inputs").status_code==403
    def anonymous():raise HTTPException(401,"Authentication required")
    app.dependency_overrides[require_authenticated_principal]=anonymous
    assert client.get(f"/api/v1/branding-dna/shops/{shops[0].id}/dna-inputs").status_code==401


def test_dna_draft_with_no_verified_listing_is_blocked_and_read_only(test_context):
    _, client, shops, session = test_context
    before = session.query(BrandListingSnapshotModel).count()
    url = f"/api/v1/branding-dna/shops/{shops[0].id}/dna-draft/preview"
    response = client.get(url)
    assert response.status_code == 200
    packet = response.json()
    assert packet["status"] == "blocked"
    assert packet["preview_only"] is True and packet["persisted"] is False
    assert packet["model_invoked"] is False
    assert packet["dna_generation_enabled"] is False
    assert packet["visual_analysis_enabled"] is False
    assert packet["image_rights_verified"] is False
    assert packet["best_seller_verified"] is False
    assert packet["brand_pick_verified"] is False
    assert packet["verified_product_examples"] == []
    assert packet["observed_categories"] == []
    assert "verified_product_evidence_missing" in packet["blockers"]
    assert session.query(BrandListingSnapshotModel).count() == before
    assert response.headers["cache-control"] == "no-store"


def test_verified_product_facts_are_cited_but_rag_is_not_product_proof(test_context):
    _, client, shops, session = test_context
    shop = shops[0]
    document = make_doc(session, shop, approved=True)
    first = BrandListingSnapshotModel(
        tenant_id=shop.tenant_id, shop_id=shop.id,
        listing_key="source-100", source_revision="r1",
        export_reference="manual-verification-100",
        title="Verified Cotton Cap", category="caps",
        source_kind="authorized_export", evidence_state="verified",
        coverage_state="unknown",
        captured_at=datetime.now(timezone.utc), imported_by="operator",
        content_sha256="c"*64,
    )
    second = BrandListingSnapshotModel(
        tenant_id=shop.tenant_id, shop_id=shop.id,
        listing_key="source-101", source_revision="r1",
        export_reference="manual-verification-101",
        title="Verified Twill Hat", category="caps",
        source_kind="authorized_export", evidence_state="verified",
        coverage_state="unknown",
        captured_at=datetime.now(timezone.utc), imported_by="operator",
        content_sha256="d"*64,
    )
    unverified = BrandListingSnapshotModel(
        tenant_id=shop.tenant_id, shop_id=shop.id,
        listing_key="source-999", source_revision="r1",
        export_reference="unverified-export",
        title="Unsupported Bestseller Claim", category="top seller",
        source_kind="authorized_export", evidence_state="imported_unverified",
        coverage_state="unknown",
        captured_at=datetime.now(timezone.utc), imported_by="operator",
        content_sha256="e"*64,
    )
    session.add_all([first,second,unverified]);session.commit()
    url = f"/api/v1/branding-dna/shops/{shop.id}/dna-draft/preview"
    a = client.get(url)
    b = client.get(url)
    assert a.status_code == 200
    packet = a.json()
    assert packet["status"] == "evidence_outline"
    assert packet["verified_listing_count"] == 2
    assert packet["observed_categories"] == [{"category": "caps", "listing_count": 2}]
    assert {x["citation"] for x in packet["verified_product_examples"]} == {
        f"listing:{first.id}",f"listing:{second.id}",
    }
    assert all(x["source_state"] == "verified_listing_metadata_only"
               for x in packet["verified_product_examples"])
    assert packet["context_references"][0]["citation"] == f"rag:{document.id}:0"
    assert packet["context_references"][0]["trust"] == (
        "human_approved_context_not_independent_product_proof")
    assert "Warm shop voice" not in str(packet)
    assert "Unsupported Bestseller Claim" not in str(packet)
    assert packet["input_fingerprint"] == b.json()["input_fingerprint"]
    assert packet["image_rights_verified"] is False
    assert packet["best_seller_verified"] is False
    assert packet["persisted"] is False


def test_draft_does_not_expose_other_tenant_or_non_privileged_data(test_context):
    app, client, shops, _ = test_context
    url = f"/api/v1/branding-dna/shops/{shops[0].id}/dna-draft/preview"
    app.dependency_overrides[require_authenticated_principal] = lambda: principal("b")
    assert client.get(url).status_code == 404
    app.dependency_overrides[require_authenticated_principal] = lambda: principal(privileged=False)
    assert client.get(url).status_code == 403
    def anonymous(): raise HTTPException(401,"Authentication required")
    app.dependency_overrides[require_authenticated_principal] = anonymous
    assert client.get(url).status_code == 401


def test_large_verified_shop_returns_bounded_non_final_outline(test_context):
    _,client,shops,session = test_context
    shop=shops[0]
    for i in range(13):
        session.add(BrandListingSnapshotModel(
            tenant_id=shop.tenant_id,shop_id=shop.id,
            listing_key=f"listing-{i}",source_revision="r1",
            export_reference="authorized-export",title=f"Cap {i}",
            category="caps",source_kind="authorized_export",
            evidence_state="verified",coverage_state="unknown",
            captured_at=datetime.now(timezone.utc),imported_by="operator",
            content_sha256=f"{i:064x}",
        ))
    session.commit()
    packet=client.get(
        f"/api/v1/branding-dna/shops/{shop.id}/dna-draft/preview"
    ).json()
    assert packet["status"] == "blocked"
    assert packet["verified_listing_count"] == 13
    assert packet["examples_truncated"] is True
    assert len(packet["verified_product_examples"]) == 12
    assert "draft_example_sample_truncated" in packet["blockers"]
    assert packet["persisted"] is False
