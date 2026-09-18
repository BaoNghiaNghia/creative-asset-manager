import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import Base
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.auth_persistence.model import OAuthConnectionModel, TenantModel
from app.modules.public_review.model import PublicReviewRateLimitModel
from app.modules.authorization.folder_scope_cache import viewer_folder_hierarchy_cache
import app.modules.public_review.public_router as public_router
from app.modules.public_review.public_router import COOKIE, router
from app.modules.public_review.repository import PublicReviewRepository
from app.modules.public_review.service import PublicReviewService

@pytest.fixture()
def ctx():
 engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
 event.listen(engine,"connect",lambda c,_: c.execute("PRAGMA foreign_keys=ON")); Base.metadata.create_all(engine); factory=sessionmaker(engine,class_=Session,expire_on_commit=False)
 with factory() as s:
  s.add_all([TenantModel(id="tenant-a",name="A",slug="a"),TenantModel(id="tenant-b",name="B",slug="b")]);s.flush()
  s.add_all([OAuthConnectionModel(id="conn-a",tenant_id="tenant-a",provider="google",provider_account_id="a",key_version="v1"),OAuthConnectionModel(id="conn-b",tenant_id="tenant-b",provider="google",provider_account_id="b",key_version="v1")]);s.flush()
  s.add_all([ExternalSourceModel(id="source-a",tenant_id="tenant-a",source_key="a",source_type="google_drive",oauth_connection_id="conn-a"),ExternalSourceModel(id="source-b",tenant_id="tenant-b",source_key="b",source_type="google_drive",oauth_connection_id="conn-b")]);s.flush()
  s.add_all([SourceAssetModel(id="root",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="root",filename="Root"),SourceAssetModel(id="child",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="child",filename="cat-good.jpg",mime_type="image/jpeg",source_metadata={"parents":["root"]}),SourceAssetModel(id="sibling",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="sibling",filename="cat-private.jpg",mime_type="image/jpeg",source_metadata={"parents":["other"]}),SourceAssetModel(id="foreign",tenant_id="tenant-b",external_source_id="source-b",external_asset_id="root",filename="cat-foreign.jpg",mime_type="image/jpeg")]);s.flush()
  s.add_all([AssetModel(id="asset-good",tenant_id="tenant-a",content_hash="a"*64),AssetModel(id="asset-private",tenant_id="tenant-a",content_hash="b"*64),AssetModel(id="asset-foreign",tenant_id="tenant-b",content_hash="c"*64)]);s.flush();s.add_all([AssetSourceLinkModel(id="l1",tenant_id="tenant-a",asset_id="asset-good",source_asset_id="child"),AssetSourceLinkModel(id="l2",tenant_id="tenant-a",asset_id="asset-private",source_asset_id="sibling"),AssetSourceLinkModel(id="l3",tenant_id="tenant-b",asset_id="asset-foreign",source_asset_id="foreign"),AssetSourceLinkModel(id="l4",tenant_id="tenant-a",asset_id="asset-good",source_asset_id="sibling")]);s.flush()
  service=PublicReviewService(PublicReviewRepository(s));share=service.create_share(tenant_id="tenant-a",public_id="share-a",name="Review",raw_secret="fake-public-secret",created_by="u");PublicReviewRepository(s).replace_scopes("tenant-a",share.id,[{"external_source_id":"source-a","folder_external_id":"root"}]);s.commit()
 app=FastAPI();app.include_router(router); yield TestClient(app),factory,share;engine.dispose()
def request(ctx,method,path,**kw):
 with patch("app.modules.public_review.public_router.SessionLocal",ctx[1]): return ctx[0].request(method,path,**kw)
def exchange(ctx,secret="fake-public-secret"):
 return request(ctx,"POST","/api/public/review/share-a/session",json={"key":secret},headers={"Origin":"http://localhost:5173"})
def test_session_exchange_headers_and_generic_denial(ctx):
 good=exchange(ctx);assert good.status_code==201 and "fake-public-secret" not in good.text
 cookie=good.headers["set-cookie"].lower();assert "httponly" in cookie and "samesite=lax" in cookie
 missing=request(ctx,"POST","/api/public/review/share-a/session",json={"key":"fake-public-secret"});wrong=request(ctx,"POST","/api/public/review/missing/session",json={"key":"bad"},headers={"Origin":"http://localhost:5173"});assert missing.status_code==wrong.status_code==404 and missing.json()==wrong.json()
 with ctx[1]() as s:
  assert s.scalar(select(PublicReviewRateLimitModel)) is not None
  assert "198.51.100" not in str(s.scalar(select(PublicReviewRateLimitModel)).client_digest)
def test_scoped_browse_asset_and_search(ctx):
 assert exchange(ctx).status_code==201
 folders=request(ctx,"GET","/api/public/review/share-a/folders");assert folders.status_code==200 and folders.json()["items"][0]["folder_id"]=="root"
 child=request(ctx,"GET","/api/public/review/share-a/folders/root/children?source_id=source-a");assert child.status_code==200 and child.json()["items"][0]["kind"]=="asset" and child.json()["items"][0]["asset_id"]=="asset-good" and child.json()["items"][0]["source_asset_id"]=="child"
 foreign=request(ctx,"GET","/api/public/review/share-a/folders/root/children?source_id=source-b");assert foreign.status_code==404
 allowed=request(ctx,"GET","/api/public/review/share-a/assets/asset-good?source_asset_id=child");denied=request(ctx,"GET","/api/public/review/share-a/assets/asset-private?source_asset_id=sibling");assert allowed.status_code==200 and denied.status_code==404 and "source_metadata" not in allowed.text
 found=request(ctx,"GET","/api/public/review/share-a/search?q=cat");assert found.status_code==200 and [x["asset_id"] for x in found.json()["items"]]==["asset-good"] and "private" not in found.text

def note_body():
 return {"content_json":{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"hello"}]}]},"anchor_x":None,"anchor_y":None}
def test_anonymous_annotation_origin_and_ownership(ctx):
 assert exchange(ctx).status_code==201
 path="/api/public/review/share-a/assets/asset-good/annotations?source_asset_id=child"
 assert request(ctx,"POST",path,json=note_body()).status_code==404
 created=request(ctx,"POST",path,json=note_body(),headers={"Origin":"http://localhost:5173"})
 assert created.status_code==201 and created.json()["plain_text"]=="hello" and created.json()["can_edit"] and created.json()["author"]["display_name"].startswith("Guest ") and "guest_id" not in created.text and "session" not in created.text
 annotation_id=created.json()["id"]
 assert request(ctx,"GET",path).status_code==200
 assert request(ctx,"PATCH","/api/public/review/share-a/annotations/"+annotation_id,json={"content_json":note_body()["content_json"]},headers={"Origin":"http://localhost:5173"}).status_code==200
 assert request(ctx,"DELETE","/api/public/review/share-a/annotations/"+annotation_id,headers={"Origin":"http://localhost:5173"}).status_code==200

def test_annotation_mutations_revalidate_the_exact_asset_source_scope(ctx):
 assert exchange(ctx).status_code==201
 path="/api/public/review/share-a/assets/asset-good/annotations?source_asset_id=child"
 created=request(ctx,"POST",path,json=note_body(),headers={"Origin":"http://localhost:5173"})
 assert created.status_code==201
 annotation_id=created.json()["id"]
 with ctx[1]() as s:
  PublicReviewRepository(s).replace_scopes("tenant-a",ctx[2].id,[{"external_source_id":"source-a","folder_external_id":"other"}]);s.commit()
 assert request(ctx,"GET","/api/public/review/share-a/assets/asset-good?source_asset_id=sibling").status_code==200
 patch=request(ctx,"PATCH","/api/public/review/share-a/annotations/"+annotation_id,json={"content_json":note_body()["content_json"]},headers={"Origin":"http://localhost:5173"})
 delete=request(ctx,"DELETE","/api/public/review/share-a/annotations/"+annotation_id,headers={"Origin":"http://localhost:5173"})
 assert patch.status_code==delete.status_code==404
 assert patch.json()==delete.json()=={"detail":{"code":"public_review_unavailable"}}

def test_annotation_mutation_denies_foreign_guest_and_comment_disabled(ctx):
 assert exchange(ctx).status_code==201
 first_token=ctx[0].cookies.get(COOKIE)
 path="/api/public/review/share-a/assets/asset-good/annotations?source_asset_id=child"
 created=request(ctx,"POST",path,json=note_body(),headers={"Origin":"http://localhost:5173"})
 assert created.status_code==201
 annotation_id=created.json()["id"]
 assert exchange(ctx).status_code==201
 foreign_patch=request(ctx,"PATCH","/api/public/review/share-a/annotations/"+annotation_id,json={"content_json":note_body()["content_json"]},headers={"Origin":"http://localhost:5173"})
 foreign_delete=request(ctx,"DELETE","/api/public/review/share-a/annotations/"+annotation_id,headers={"Origin":"http://localhost:5173"})
 assert foreign_patch.status_code==foreign_delete.status_code==404
 ctx[0].cookies.set(COOKIE,first_token)
 with ctx[1]() as s:
  share=s.merge(ctx[2]);share.allow_comments=False;s.commit()
 disabled_patch=request(ctx,"PATCH","/api/public/review/share-a/annotations/"+annotation_id,json={"content_json":note_body()["content_json"]},headers={"Origin":"http://localhost:5173"})
 disabled_delete=request(ctx,"DELETE","/api/public/review/share-a/annotations/"+annotation_id,headers={"Origin":"http://localhost:5173"})
 assert disabled_patch.status_code==disabled_delete.status_code==404
 assert disabled_patch.json()==disabled_delete.json()=={"detail":{"code":"public_review_unavailable"}}

def test_pinned_annotation_bounds_and_text_edits_preserve_anchor(ctx):
    assert exchange(ctx).status_code == 201
    path = "/api/public/review/share-a/assets/asset-good/annotations?source_asset_id=child"
    for body in ({**note_body(), "anchor_x": -.01, "anchor_y": .5}, {**note_body(), "anchor_x": 1.01, "anchor_y": .5}, {**note_body(), "anchor_x": .5, "anchor_y": -.01}, {**note_body(), "anchor_x": .5, "anchor_y": 1.01}, {**note_body(), "anchor_x": .5}):
        assert request(ctx, "POST", path, json=body, headers={"Origin": "http://localhost:5173"}).status_code == 404
    created = request(ctx, "POST", path, json={**note_body(), "anchor_x": .25, "anchor_y": .75}, headers={"Origin": "http://localhost:5173"})
    assert created.status_code == 201
    updated = request(ctx, "PATCH", "/api/public/review/share-a/annotations/" + created.json()["id"], json={"content_json": note_body()["content_json"]}, headers={"Origin": "http://localhost:5173"})
    assert updated.status_code == 200
    assert updated.json()["anchor_x"] == .25 and updated.json()["anchor_y"] == .75


def test_scoped_folder_pagination_keeps_image_and_video_visible(ctx):
 assert exchange(ctx).status_code == 201
 with ctx[1]() as s:
  video_source=SourceAssetModel(id="video-child",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="video-child",filename="clip-good.mp4",mime_type="video/mp4",source_metadata={"parents":["root"]})
  video_asset=AssetModel(id="asset-video",tenant_id="tenant-a",content_hash="d"*64)
  s.add_all([video_source,video_asset]);s.flush()
  s.add(AssetSourceLinkModel(id="l-video",tenant_id="tenant-a",asset_id="asset-video",source_asset_id="video-child"));s.commit()
 viewer_folder_hierarchy_cache.invalidate(tenant_id="tenant-a",external_source_id="source-a")
 first=request(ctx,"GET","/api/public/review/share-a/folders/root/children?source_id=source-a&limit_value=1")
 assert first.status_code == 200 and first.json()["next_offset"] == 1
 second=request(ctx,"GET","/api/public/review/share-a/folders/root/children?source_id=source-a&limit_value=1&offset="+str(first.json()["next_offset"]))
 assert second.status_code == 200 and second.json()["next_offset"] is None
 items=first.json()["items"]+second.json()["items"]
 assert {item["media_type"] for item in items} == {"image/jpeg","video/mp4"}
 assert {item["asset_id"] for item in items} == {"asset-good","asset-video"}


def test_public_media_guard_releases_the_limited_slot(monkeypatch):
 before=public_router._public_media_slots._value
 principal=SimpleNamespace(tenant_id="tenant-a")
 source=SimpleNamespace(id="child",mime_type="image/jpeg")
 class Resolver:
  def __init__(self,*_): pass
  @asynccontextmanager
  async def open(self,**_):
   yield SimpleNamespace(body=chunks())
 async def chunks():
  yield b"image"
 monkeypatch.setattr(public_router,"user",lambda *_: principal)
 monkeypatch.setattr(public_router,"asset_pair",lambda *_: (SimpleNamespace(),source))
 monkeypatch.setattr(public_router,"SourceAssetContentResolver",Resolver)
 async def consume():
  response=await public_router.media("share-a","asset-good",SimpleNamespace(headers={}),"child")
  return [chunk async for chunk in response.body_iterator]
 assert asyncio.run(consume())==[b"image"]
 assert public_router._public_media_slots._value==before


def test_public_thumbnail_uses_bounded_thumbnail_resolver_not_original_media(monkeypatch):
 principal=SimpleNamespace(tenant_id="tenant-a")
 source=SimpleNamespace(id="child")
 class Resolver:
  def __init__(self,*_): pass
  async def load(self,**kwargs):
   assert kwargs == {"tenant_id":"tenant-a","source_asset_id":"child"}
   return SimpleNamespace(content=b"thumbnail",content_type="image/webp")
 monkeypatch.setattr(public_router,"user",lambda *_: principal)
 monkeypatch.setattr(public_router,"asset_pair",lambda *_: (SimpleNamespace(),source))
 monkeypatch.setattr(public_router,"PublicThumbnailResolver",Resolver)
 async def request_thumbnail():
  return await public_router.thumbnail("share-a","asset-good",SimpleNamespace(),"child")
 response=asyncio.run(request_thumbnail())
 assert response.body == b"thumbnail"
 assert response.media_type == "image/webp"
 assert response.headers["cache-control"] == "no-store, private"
