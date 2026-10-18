"""HTTP authorization, upload, and shop isolation for RAG documents."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.branding_dna.knowledge_model import BrandKnowledgeChunkModel, BrandKnowledgeDocumentModel
from app.modules.branding_dna.knowledge_router import router as knowledge_router
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.router import ShopCreate, add_shop


def actor(*perms, tenant="tenant-1"):
    return SimpleNamespace(
        active_tenant_id=tenant, actor_id="rag-operator",
        effective_permissions=frozenset(perms), platform_admin=False,
    )


@pytest.fixture
def app_client():
    engine = create_engine("sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandKnowledgeDocumentModel.__table__,
        BrandKnowledgeChunkModel.__table__,
    ])
    session = Session(engine)
    a=add_shop(ShopCreate(platform="etsy",
        storefront_url="https://www.etsy.com/shop/RAGDemo",access_mode="authorized"),
        session=session, principal=actor("ai_provider.configure"))
    b=add_shop(ShopCreate(platform="amazon",
        storefront_url="https://www.amazon.com/stores/RAGDemo",access_mode="authorized"),
        session=session, principal=actor("ai_provider.configure",tenant="tenant-2"))
    app=FastAPI()
    app.include_router(knowledge_router)
    app.dependency_overrides[get_db]=lambda: session
    app.dependency_overrides[require_authenticated_principal]=lambda: actor("ai_operations.read")
    with TestClient(app) as client:
        yield app,client,a["id"],b["id"],session
    session.close()
    engine.dispose()


def upload(client,url):
    return client.post(url,files={"file":("tone.md",b"Shop voice: warm and direct, with soft ocean blues and natural product photography.","text/markdown")},
                       data={"category":"brand_voice","source_note":"Owner brand style manual 2026"})


def test_http_rag_read_only_cannot_upload_or_review(app_client):
    app,client,shop_id,other,_=app_client
    root=f"/api/v1/branding-dna/shops/{shop_id}/knowledge"
    assert client.get(root).status_code==403
    assert upload(client,root).status_code==403
    assert client.post(root+"/search",json={"query":"ocean blues"}).status_code==403


def test_http_rag_upload_review_search_and_archive(app_client):
    app,client,shop_id,_,session=app_client
    app.dependency_overrides[require_authenticated_principal]=lambda: actor(
        "ai_operations.read","ai_provider.configure")
    root=f"/api/v1/branding-dna/shops/{shop_id}/knowledge"
    posted=upload(client,root)
    assert posted.status_code==201,posted.text
    item=posted.json()["item"]
    assert item["status"]=="pending_review"
    url=root+"/"+item["id"]
    assert upload(client,root).status_code==409
    assert client.post(root+"/search",json={"query":"ocean blues"}).json()["items"]==[]
    chunks=client.get(url+"/chunks")
    assert chunks.status_code==200
    assert chunks.json()["chunks"][0]["citation"]==f'rag:{item["id"]}:0'
    assert client.post(url+"/review",json={"action":"approve","note":""}).status_code==422
    approved=client.post(url+"/review",json={"action":"approve","note":"Confirmed owner document"})
    assert approved.status_code==200,approved.text
    assert approved.json()["item"]["status"]=="approved"
    found=client.post(root+"/search",json={"query":"ocean blues","limit":5})
    assert found.status_code==200
    assert len(found.json()["items"])==1
    assert found.json()["items"][0]["document_id"]==item["id"]
    assert found.json()["auto_apply_to_dna"] is False
    assert client.post(root+"/search",json={"query":"ocean blues","limit":200}).status_code==422
    assert client.post(url+"/review",json={"action":"approve","note":"Retry"}).status_code==409
    archived=client.post(url+"/review",json={"action":"archive","note":"Old reference"})
    assert archived.status_code==200
    assert client.post(root+"/search",json={"query":"ocean blues"}).json()["items"]==[]
    assert session.query(BrandKnowledgeChunkModel).count()==1
    assert chunks.headers["cache-control"]=="no-store"


def test_http_tenant_and_shop_boundaries(app_client):
    app,client,shop_id,other,_=app_client
    app.dependency_overrides[require_authenticated_principal]=lambda: actor(
        "ai_operations.read","ai_provider.configure")
    root=f"/api/v1/branding-dna/shops/{shop_id}/knowledge"
    data=upload(client,root).json()
    doc_id=data["item"]["id"]
    other_root=f"/api/v1/branding-dna/shops/{other}/knowledge"
    assert client.get(other_root).status_code==404
    assert upload(client,other_root).status_code==404
    app.dependency_overrides[require_authenticated_principal]=lambda: actor(
        "ai_operations.read","ai_provider.configure",tenant="tenant-2")
    assert client.get(root).status_code==404
    assert client.get(root+"/"+doc_id+"/chunks").status_code==404
    assert client.post(root+"/search",json={"query":"ocean blues"}).status_code==404
    assert client.post(root+"/"+doc_id+"/review",json={"action":"archive"}).status_code==404


def test_http_anonymous_rejected(app_client):
    app,client,shop_id,_,_=app_client
    def anonymous(): raise HTTPException(status_code=401,detail="authentication_required")
    app.dependency_overrides[require_authenticated_principal]=anonymous
    root=f"/api/v1/branding-dna/shops/{shop_id}/knowledge"
    assert client.get(root).status_code==401
    assert upload(client,root).status_code==401
