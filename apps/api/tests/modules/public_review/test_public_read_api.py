import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from fastapi import FastAPI, HTTPException
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
  s.add_all([SourceAssetModel(id="root",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="root",filename="Root",source_metadata={"is_folder":True}),SourceAssetModel(id="child",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="child",filename="cat-good.jpg",mime_type="image/jpeg",source_metadata={"parents":["root"]}),SourceAssetModel(id="sibling",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="sibling",filename="cat-private.jpg",mime_type="image/jpeg",source_metadata={"parents":["other"]}),SourceAssetModel(id="sibling-copy",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="sibling-copy",filename="cat-good-copy.jpg",mime_type="image/jpeg",source_metadata={"parents":["other"]}),SourceAssetModel(id="foreign",tenant_id="tenant-b",external_source_id="source-b",external_asset_id="root",filename="cat-foreign.jpg",mime_type="image/jpeg")]);s.flush()
  s.add_all([AssetModel(id="asset-good",tenant_id="tenant-a",content_hash="a"*64),AssetModel(id="asset-private",tenant_id="tenant-a",content_hash="b"*64),AssetModel(id="asset-foreign",tenant_id="tenant-b",content_hash="c"*64)]);s.flush();s.add_all([AssetSourceLinkModel(id="l1",tenant_id="tenant-a",asset_id="asset-good",source_asset_id="child"),AssetSourceLinkModel(id="l2",tenant_id="tenant-a",asset_id="asset-private",source_asset_id="sibling"),AssetSourceLinkModel(id="l3",tenant_id="tenant-b",asset_id="asset-foreign",source_asset_id="foreign"),AssetSourceLinkModel(id="l4",tenant_id="tenant-a",asset_id="asset-good",source_asset_id="sibling-copy")]);s.flush()
  service=PublicReviewService(PublicReviewRepository(s));share=service.create_share(tenant_id="tenant-a",public_id="share-a",name="Review",raw_secret="fake-public-secret",created_by="u");PublicReviewRepository(s).replace_scopes("tenant-a",share.id,[{"external_source_id":"source-a","folder_external_id":"root"}]);s.commit()
 app=FastAPI();app.include_router(router); yield TestClient(app),factory,share;engine.dispose()
def request(ctx,method,path,**kw):
 with patch("app.modules.public_review.public_router.SessionLocal",ctx[1]): return ctx[0].request(method,path,**kw)
def exchange(ctx,secret="fake-public-secret"):
 return request(ctx,"POST","/api/public/review/share-a/session",json={"key":secret},headers={"Origin":"http://localhost:5173"})
def test_session_exchange_headers_and_generic_denial(ctx):
 good=exchange(ctx);assert good.status_code==201 and "fake-public-secret" not in good.text
 cookie=good.headers["set-cookie"].lower();assert "httponly" in cookie and "samesite=lax" in cookie
 max_age=re.search(r"max-age=(\d+)",cookie);assert max_age and 604700 <= int(max_age.group(1)) <= 604800
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

def test_public_folder_resolution_returns_authorized_breadcrumb(ctx):
 assert exchange(ctx).status_code==201
 with ctx[1]() as s:
  s.add_all([
   SourceAssetModel(id="nested-folder",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="nested",filename="Nested",source_metadata={"is_folder":True,"parents":["root"]}),
   SourceAssetModel(id="private-folder",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="private-folder",filename="Private",source_metadata={"is_folder":True,"parents":["other"]}),
  ])
  s.commit()
 viewer_folder_hierarchy_cache.invalidate(tenant_id="tenant-a",external_source_id="source-a")
 resolved=request(ctx,"GET","/api/public/review/share-a/folders/nested")
 assert resolved.status_code==200
 assert resolved.json()=={
  "folder":{"source_id":"source-a","folder_id":"nested","name":"Nested"},
  "trail":[
   {"source_id":"source-a","folder_id":"root","name":"Root"},
   {"source_id":"source-a","folder_id":"nested","name":"Nested"},
  ],
 }
 denied=request(ctx,"GET","/api/public/review/share-a/folders/private-folder")
 assert denied.status_code==404

def note_body():
 return {"content_json":{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"hello"}]}]},"anchor_x":None,"anchor_y":None}

def test_public_asset_cards_include_annotation_count(ctx):
 assert exchange(ctx).status_code==201
 path="/api/public/review/share-a/assets/asset-good/annotations?source_asset_id=child"
 first=request(ctx,"POST",path,json=note_body(),headers={"Origin":"http://localhost:5173"})
 assert first.status_code==201
 reply=request(ctx,"POST",path,json={**note_body(),"parent_annotation_id":first.json()["id"]},headers={"Origin":"http://localhost:5173"})
 assert reply.status_code==201
 child=request(ctx,"GET","/api/public/review/share-a/folders/root/children?source_id=source-a")
 assert child.status_code==200
 item=next(value for value in child.json()["items"] if value.get("asset_id")=="asset-good")
 assert item["annotation_count"]==2
 metadata=request(ctx,"GET","/api/public/review/share-a/assets/asset-good?source_asset_id=child")
 assert metadata.status_code==200 and metadata.json()["annotation_count"]==2
 found=request(ctx,"GET","/api/public/review/share-a/search?q=cat")
 assert found.status_code==200 and found.json()["items"][0]["annotation_count"]==2
 comments=request(ctx,"GET","/api/public/review/share-a/comments")
 assert comments.status_code==200
 assert len(comments.json()["items"])==2
 assert comments.json()["items"][0]["asset"]["asset_id"]=="asset-good"
 assert comments.json()["items"][0]["asset"]["source_asset_id"]=="child"
 assert comments.json()["items"][0]["annotation"]["plain_text"]=="hello"

def test_public_search_uses_search_v3_with_share_scope_and_hydrates_only_allowed_assets(ctx):
 assert exchange(ctx).status_code==201
 captured={}
 class Index:
  async def search(self,query):
   captured["query"]=query
   return {
    "took":7,
    "hits":{"hits":[
     {"_id":"asset-good","_score":4.2,"_source":{"asset_id":"asset-good","source_id":"source-a","filename":"cat-good.jpg"}},
     {"_id":"asset-private","_score":3.1,"_source":{"asset_id":"asset-private","source_id":"source-a","filename":"cat-private.jpg"}},
    ]},
   }
 class Pool:
  async def get(self,_config):
   return Index()
 configured=SimpleNamespace(
  SEARCH_V3_ENABLED=True,
  SEARCH_V3_REQUIRED=True,
  ELASTICSEARCH_URL="http://search.test:9200",
  ELASTICSEARCH_INDEX_PREFIX="creative-assets",
 )
 with patch("app.modules.public_review.public_router.get_settings",return_value=configured), \
      patch("app.modules.public_review.public_router._search_generation",return_value="ready"), \
      patch("app.modules.public_review.public_router.API_SEARCH_INDEX_POOL",Pool()):
  found=request(ctx,"GET","/api/public/review/share-a/search?q=cat")
 assert found.status_code==200
 payload=found.json()
 assert payload["search_version"]=="v3"
 assert payload["took_ms"]==7
 assert [item["asset_id"] for item in payload["items"]]==["asset-good"]
 filters=captured["query"]["query"]["bool"]["filter"]
 assert {"term":{"tenant_id":"tenant-a"}} in filters
 scope_filter=next(row for row in filters if "bool" in row and "should" in row["bool"])
 assert scope_filter["bool"]["minimum_should_match"]==1
 assert scope_filter["bool"]["should"]==[{"bool":{"filter":[{"term":{"source_id":"source-a"}},{"terms":{"ancestor_ids":["root"]}}]}}]



def test_public_search_suggestions_use_share_scope_and_exclude_private_assets(ctx):
 assert exchange(ctx).status_code==201
 captured={}
 class Index:
  async def search(self,query):
   captured["query"]=query
   return {
    "took":3,
    "hits":{"hits":[
     {"_id":"asset-good","_source":{"asset_id":"asset-good","source_id":"source-a","filename":"cat-good.jpg","visible_text":"cat campaign summer"}},
     {"_id":"asset-private","_source":{"asset_id":"asset-private","source_id":"source-a","filename":"cat-private.jpg","visible_text":"cat private secret"}},
    ]},
   }
 class Pool:
  async def get(self,_config):
   return Index()
 configured=SimpleNamespace(
  SEARCH_V3_ENABLED=True,
  SEARCH_V3_REQUIRED=True,
  ELASTICSEARCH_URL="http://search.test:9200",
  ELASTICSEARCH_INDEX_PREFIX="creative-assets",
 )
 with patch("app.modules.public_review.public_router.get_settings",return_value=configured), \
      patch("app.modules.public_review.public_router._search_generation",return_value="ready"), \
      patch("app.modules.public_review.public_router.API_SEARCH_INDEX_POOL",Pool()):
  found=request(ctx,"GET","/api/public/review/share-a/search/suggestions?q=cat")
 assert found.status_code==200
 payload=found.json()
 assert payload["search_version"]=="v3"
 assert payload["took_ms"]==3
 texts=[item["text"] for item in payload["suggestions"]]
 assert texts
 assert all("private" not in text.casefold() and "secret" not in text.casefold() for text in texts)
 assert any("cat" in text.casefold() for text in texts)
 filters=captured["query"]["query"]["bool"]["filter"]
 assert {"term":{"tenant_id":"tenant-a"}} in filters
 scope_filter=next(row for row in filters if "bool" in row and "should" in row["bool"])
 assert scope_filter["bool"]["should"]==[{"bool":{"filter":[{"term":{"source_id":"source-a"}},{"terms":{"ancestor_ids":["root"]}}]}}]


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
 assert request(ctx,"GET","/api/public/review/share-a/assets/asset-good?source_asset_id=sibling-copy").status_code==200
 comments=request(ctx,"GET","/api/public/review/share-a/comments")
 assert comments.status_code==200 and comments.json()["items"]==[]
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
  pending_source=SourceAssetModel(id="pending-child",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="pending-child",filename="aaa-pending.mp4",mime_type="application/octet-stream",source_metadata={"parents":["root"]})
  video_source=SourceAssetModel(id="video-child",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="video-child",filename="clip-good.mp4",mime_type="application/octet-stream",source_metadata={"parents":["root"]})
  video_asset=AssetModel(id="asset-video",tenant_id="tenant-a",content_hash="d"*64)
  s.add_all([pending_source,video_source,video_asset]);s.flush()
  s.add(AssetSourceLinkModel(id="l-video",tenant_id="tenant-a",asset_id="asset-video",source_asset_id="video-child"));s.commit()
 viewer_folder_hierarchy_cache.invalidate(tenant_id="tenant-a",external_source_id="source-a")
 first=request(ctx,"GET","/api/public/review/share-a/folders/root/children?source_id=source-a&limit_value=1")
 assert first.status_code == 200 and first.json()["next_offset"] == 1
 second=request(ctx,"GET","/api/public/review/share-a/folders/root/children?source_id=source-a&limit_value=1&offset="+str(first.json()["next_offset"]))
 assert second.status_code == 200 and second.json()["next_offset"] is None
 items=first.json()["items"]+second.json()["items"]
 assert {item["media_type"] for item in items} == {"image/jpeg","video/mp4"}
 assert {item["asset_id"] for item in items} == {"asset-good","asset-video"}


def test_public_media_guard_releases_the_limited_slot_and_forwards_range(monkeypatch):
 before=public_router._public_media_slots._value
 principal=SimpleNamespace(tenant_id="tenant-a")
 source=SimpleNamespace(id="child",filename="image.jpg",mime_type="image/jpeg")
 class Resolver:
  def __init__(self,*_): pass
  @asynccontextmanager
  async def open(self,**kwargs):
   assert kwargs["range_header"]=="bytes=0-4"
   yield SimpleNamespace(
    body=chunks(),
    status_code=206,
    content_type="image/jpeg",
    headers={"accept-ranges":"bytes","content-range":"bytes 0-4/5","content-length":"5"},
   )
 async def chunks():
  yield b"image"
 monkeypatch.setattr(public_router,"user",lambda *_: principal)
 monkeypatch.setattr(public_router,"asset_pair",lambda *_: (SimpleNamespace(),source))
 monkeypatch.setattr(public_router,"SourceAssetContentResolver",Resolver)
 async def consume():
  response=await public_router.media("share-a","asset-good",SimpleNamespace(headers={"range":"bytes=0-4"}),"child")
  assert response.status_code==206
  assert response.headers["accept-ranges"]=="bytes"
  assert response.headers["content-range"]=="bytes 0-4/5"
  assert response.headers["content-length"]=="5"
  return [chunk async for chunk in response.body_iterator]
 assert asyncio.run(consume())==[b"image"]
 assert public_router._public_media_slots._value==before


def test_public_thumbnail_uses_bounded_thumbnail_resolver_not_original_media(monkeypatch):
 principal=SimpleNamespace(tenant_id="tenant-a")
 source=SimpleNamespace(id="child",external_source_id="source-a",external_asset_id="file-a",filename="image.jpg",mime_type="image/jpeg",source_metadata={"thumbnail_url":"https://lh3.googleusercontent.com/example=s220"})
 google_client=object()
 onedrive_client=object()
 class Resolver:
  def __init__(self,*_,google_http_client=None,onedrive_http_client=None):
   assert google_http_client is google_client
   assert onedrive_http_client is onedrive_client
  async def load(self,**kwargs):
   assert kwargs == {"tenant_id":"tenant-a","external_source_id":"source-a","external_asset_id":"file-a","filename":"image.jpg","mime_type":"image/jpeg","thumbnail_url_hint":"https://lh3.googleusercontent.com/example=s220","variant":"grid"}
   return SimpleNamespace(content=b"thumbnail",content_type="image/webp")
 monkeypatch.setattr(public_router,"user",lambda *_: principal)
 monkeypatch.setattr(public_router,"asset_pair",lambda *_: (SimpleNamespace(),source))
 monkeypatch.setattr(public_router,"PublicThumbnailResolver",Resolver)
 async def request_thumbnail(headers=None):
  state=SimpleNamespace(google_drive_stream_client=google_client,onedrive_stream_client=onedrive_client)
  request=SimpleNamespace(app=SimpleNamespace(state=state),headers=headers or {})
  return await public_router.thumbnail("share-a","asset-good",request,"child")
 response=asyncio.run(request_thumbnail())
 assert response.body == b"thumbnail"
 assert response.media_type == "image/webp"
 assert response.headers["cache-control"] == "private, no-cache, max-age=0, must-revalidate"
 assert response.headers["etag"]
 revalidated=asyncio.run(request_thumbnail({"if-none-match":response.headers["etag"]}))
 assert revalidated.status_code == 304
 assert revalidated.body == b""


def test_public_image_preview_uses_large_provider_thumbnail(monkeypatch):
 principal=SimpleNamespace(tenant_id="tenant-a")
 source=SimpleNamespace(id="child",external_source_id="source-a",external_asset_id="file-a",filename="image.jpg",mime_type="image/jpeg",source_metadata={"thumbnail_url":"https://lh3.googleusercontent.com/example=s220"})
 google_client=object()
 onedrive_client=object()
 class Resolver:
  def __init__(self,*_,google_http_client=None,onedrive_http_client=None):
   assert google_http_client is google_client
   assert onedrive_http_client is onedrive_client
  async def load(self,**kwargs):
   assert kwargs["variant"]=="preview"
   assert kwargs["thumbnail_url_hint"]=="https://lh3.googleusercontent.com/example=s220"
   return SimpleNamespace(content=b"preview",content_type="image/jpeg")
 monkeypatch.setattr(public_router,"user",lambda *_: principal)
 monkeypatch.setattr(public_router,"asset_pair",lambda *_: (SimpleNamespace(mime_type="image/jpeg"),source))
 monkeypatch.setattr(public_router,"PublicThumbnailResolver",Resolver)
 state=SimpleNamespace(google_drive_stream_client=google_client,onedrive_stream_client=onedrive_client)
 request=SimpleNamespace(app=SimpleNamespace(state=state),headers={})
 response=asyncio.run(public_router.image_preview("share-a","asset-good",request,"child"))
 assert response.body==b"preview"
 assert response.headers["cache-control"]=="private, no-cache, max-age=0, must-revalidate"


def test_public_playback_ticket_returns_direct_cdn_url(monkeypatch):
 principal=SimpleNamespace(tenant_id="tenant-a")
 asset=SimpleNamespace(id="asset-video",mime_type="video/mp4")
 source=SimpleNamespace(id="video-child",filename="clip.mp4",mime_type="video/mp4")
 class DeliveryResolver:
  def __init__(self,*_): pass
  async def resolve(self,**kwargs):
   assert kwargs=={"principal":principal,"asset":asset,"source":source}
   return SimpleNamespace(url="https://media.example.test/video?v=1&exp=2000000000&sig=safe",expires_at=2000000000)
 monkeypatch.setattr(public_router,"user",lambda *_: principal)
 monkeypatch.setattr(public_router,"asset_pair",lambda *_: (asset,source))
 monkeypatch.setattr(public_router,"PublicVideoDeliveryResolver",DeliveryResolver)
 response=asyncio.run(public_router.playback_ticket("share-a","asset-video",SimpleNamespace(),"video-child"))
 body=json.loads(response.body)
 assert body["cdn"] is True
 assert body["url"].startswith("https://media.example.test/")
 assert body["expires_at"]==2000000000


def test_public_prewarm_authorizes_and_resolves_without_returning_ticket(monkeypatch):
 principal=SimpleNamespace(tenant_id="tenant-a")
 asset=SimpleNamespace(id="asset-video",mime_type="video/mp4")
 source=SimpleNamespace(id="video-child",filename="clip.mp4",mime_type="video/mp4")
 calls=[]
 class DeliveryResolver:
  def __init__(self,*_): pass
  async def resolve(self,**kwargs):
   calls.append(kwargs)
   return None
 class DummySession:
  def __enter__(self): return self
  def __exit__(self,*_): return None
  def commit(self): pass
 monkeypatch.setattr(public_router,"origin_required",lambda *_: None)
 monkeypatch.setattr(public_router,"user",lambda *_: principal)
 monkeypatch.setattr(public_router,"asset_pair",lambda *_: (asset,source))
 monkeypatch.setattr(public_router,"PublicVideoDeliveryResolver",DeliveryResolver)
 monkeypatch.setattr(public_router,"SessionLocal",lambda:DummySession())
 monkeypatch.setattr(public_router,"limit",lambda *_args,**_kwargs: None)
 response=asyncio.run(public_router.prewarm("share-a","asset-video",SimpleNamespace(),"video-child"))
 assert response.status_code==202
 assert calls==[{"principal":principal,"asset":asset,"source":source}]


def test_public_video_cdn_redirect_skips_provider_slot_and_provider(monkeypatch):
 principal=SimpleNamespace(
  tenant_id="tenant-a",
  expires_at=None,
  session_expires_at=datetime.now(timezone.utc)+timedelta(minutes=5),
 )
 asset=SimpleNamespace(id="asset-video",tenant_id="tenant-a",content_hash="d"*64,mime_type="video/mp4")
 source=SimpleNamespace(id="video-child",tenant_id="tenant-a",filename="clip.mp4",mime_type="video/mp4")
 class DeliveryResolver:
  def __init__(self,*_): pass
  async def resolve(self,**kwargs):
   assert kwargs == {"principal":principal,"asset":asset,"source":source}
   return SimpleNamespace(url="https://media.example.test/video-cache/tenant-a/"+"d"*64+"/original?v=1&exp=2000000000&sig=safe")
 class ProviderResolver:
  def __init__(self,*_):
   raise AssertionError("provider fallback must not be created for CDN redirect")
 class NoProviderSlot:
  async def acquire(self):
   raise AssertionError("CDN redirect must not consume a provider stream slot")
  def release(self):
   raise AssertionError("CDN redirect must not release a provider stream slot")
 monkeypatch.setattr(public_router,"user",lambda *_: principal)
 monkeypatch.setattr(public_router,"asset_pair",lambda *_: (asset,source))
 monkeypatch.setattr(public_router,"PublicVideoDeliveryResolver",DeliveryResolver)
 monkeypatch.setattr(public_router,"SourceAssetContentResolver",ProviderResolver)
 monkeypatch.setattr(public_router,"_public_media_slots",NoProviderSlot())
 async def consume():
  return await public_router.media("share-a","asset-video",SimpleNamespace(headers={"range":"bytes=0-3"}),"video-child")
 response=asyncio.run(consume())
 assert response.status_code==307
 assert response.headers["location"].startswith("https://media.example.test/video-cache/tenant-a/")
 assert response.headers["cache-control"]=="no-store, private"
 assert response.headers["referrer-policy"]=="no-referrer"


def test_public_video_cdn_miss_falls_back_to_provider_with_shared_clients(monkeypatch):
 principal=SimpleNamespace(tenant_id="tenant-a")
 asset=SimpleNamespace(id="asset-video",tenant_id="tenant-a",content_hash="d"*64,mime_type="video/mp4")
 source=SimpleNamespace(id="video-child",tenant_id="tenant-a",filename="clip.mp4",mime_type="video/mp4")
 google_client=object()
 onedrive_client=object()
 class DeliveryResolver:
  def __init__(self,*_): pass
  async def resolve(self,**_): return None
 class ProviderResolver:
  def __init__(self,*_,google_http_client=None,onedrive_http_client=None):
   assert google_http_client is google_client
   assert onedrive_http_client is onedrive_client
  @asynccontextmanager
  async def open(self,**kwargs):
   assert kwargs["tenant_id"]=="tenant-a"
   assert kwargs["source_asset_id"]=="video-child"
   assert kwargs["range_header"]=="bytes=4-"
   async def chunks():
    yield b"provider-video"
   yield SimpleNamespace(
    body=chunks(),
    status_code=206,
    content_type="video/mp4",
    headers={"accept-ranges":"bytes","content-range":"bytes 4-17/18"},
   )
 monkeypatch.setattr(public_router,"user",lambda *_: principal)
 monkeypatch.setattr(public_router,"asset_pair",lambda *_: (asset,source))
 monkeypatch.setattr(public_router,"PublicVideoDeliveryResolver",DeliveryResolver)
 monkeypatch.setattr(public_router,"SourceAssetContentResolver",ProviderResolver)
 async def consume():
  state=SimpleNamespace(google_drive_stream_client=google_client,onedrive_stream_client=onedrive_client)
  request=SimpleNamespace(headers={"range":"bytes=4-"},app=SimpleNamespace(state=state))
  response=await public_router.media("share-a","asset-video",request,"video-child")
  assert response.status_code==206
  assert response.headers["content-range"]=="bytes 4-17/18"
  return [chunk async for chunk in response.body_iterator]
 assert asyncio.run(consume())==[b"provider-video"]


def test_public_preview_redirect_requires_real_exact_share_scope(ctx):
 from app.core.config import Settings
 from app.modules.video_cache.model import VIDEO_CDN_DELIVERY_SETTING_KEY, VideoCacheObjectModel, VideoDeliveryRuntimeSettingModel
 from app.modules.video_cache.service import video_cache_key
 secret="phase-4b-public-test-signing-secret-with-entropy-2026"
 with ctx[1]() as s:
  video=AssetModel(id="asset-cdn",tenant_id="tenant-a",content_hash="e"*64,mime_type="video/mp4",size_bytes=20)
  allowed_source=SourceAssetModel(id="cdn-allowed",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="cdn-allowed",filename="allowed.mp4",mime_type="video/mp4",source_metadata={"parents":["root"]})
  denied_source=SourceAssetModel(id="cdn-denied",tenant_id="tenant-a",external_source_id="source-a",external_asset_id="cdn-denied",filename="denied.mp4",mime_type="video/mp4",source_metadata={"parents":["other"]})
  s.add_all([video,allowed_source,denied_source]);s.flush()
  s.add_all([
   AssetSourceLinkModel(id="cdn-link-a",tenant_id="tenant-a",asset_id=video.id,source_asset_id=allowed_source.id),
   AssetSourceLinkModel(id="cdn-link-b",tenant_id="tenant-a",asset_id=video.id,source_asset_id=denied_source.id),
   VideoCacheObjectModel(tenant_id="tenant-a",asset_id=video.id,source_asset_id=allowed_source.id,content_hash=video.content_hash,r2_key=video_cache_key("tenant-a",video.content_hash),mime_type="video/mp4",status="ready",size_bytes=20),
   VideoDeliveryRuntimeSettingModel(setting_key=VIDEO_CDN_DELIVERY_SETTING_KEY,enabled=True),
  ])
  s.commit()
 viewer_folder_hierarchy_cache.invalidate(tenant_id="tenant-a",external_source_id="source-a")
 assert exchange(ctx).status_code==201
 configured=Settings(_env_file=None,R2_VIDEO_CACHE_ENABLED=True,R2_ACCOUNT_ID="test-account",R2_BUCKET_NAME="test-bucket",R2_ACCESS_KEY_ID="fake-id",R2_SECRET_ACCESS_KEY="fake-key",R2_VIDEO_MEDIA_BASE_URL="https://media.example.test",R2_VIDEO_MEDIA_SIGNING_SECRET=secret,VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS="tenant-a",VIDEO_CDN_DELIVERY_GUARD_ENABLED=True)
 from app.modules.video_cache.guard import VideoDeliveryCircuitBreaker
 guard=VideoDeliveryCircuitBreaker(clock=lambda:100.0)
 guard.complete_probe(configured,success=True)
 with patch("app.modules.public_review.public_router.get_settings",lambda:configured), patch("app.modules.public_review.video_delivery.VIDEO_DELIVERY_GUARD",guard), patch("app.modules.public_review.video_delivery.probe_signed_video_head",lambda *_args,**_kwargs: True):
  allowed=request(ctx,"GET","/api/public/review/share-a/assets/asset-cdn/preview?source_asset_id=cdn-allowed",follow_redirects=False)
  denied=request(ctx,"GET","/api/public/review/share-a/assets/asset-cdn/preview?source_asset_id=cdn-denied",follow_redirects=False)
 assert allowed.status_code==307
 assert allowed.headers["location"].startswith("https://media.example.test/video-cache/tenant-a/"+"e"*64+"/original?")
 assert "sig=" in allowed.headers["location"] and secret not in allowed.headers["location"]
 assert denied.status_code==404
 assert denied.json()=={"detail":{"code":"public_review_unavailable"}}


def test_public_download_requires_policy_and_streams_attachment(monkeypatch):
 principal=SimpleNamespace(tenant_id="tenant-a",allow_download=False)
 asset=SimpleNamespace(id="asset-good",mime_type="image/jpeg")
 source=SimpleNamespace(id="child",filename="summer photo.jpg",mime_type="image/jpeg")
 class Resolver:
  def __init__(self,*_,**__): pass
  @asynccontextmanager
  async def open(self,**kwargs):
   assert kwargs["tenant_id"]=="tenant-a"
   assert kwargs["source_asset_id"]=="child"
   assert kwargs["range_header"] is None
   async def chunks():
    yield b"original"
   yield SimpleNamespace(body=chunks(),status_code=200,content_type="image/jpeg",headers={"content-length":"8"})
 monkeypatch.setattr(public_router,"user",lambda *_: principal)
 monkeypatch.setattr(public_router,"asset_pair",lambda *_: (asset,source))
 monkeypatch.setattr(public_router,"SourceAssetContentResolver",Resolver)
 state=SimpleNamespace(google_drive_stream_client=None,onedrive_stream_client=None)
 request_obj=SimpleNamespace(headers={},app=SimpleNamespace(state=state))
 with pytest.raises(HTTPException) as exc:
  asyncio.run(public_router.media("share-a","asset-good",request_obj,"child",force_download=True))
 assert exc.value.status_code==404
 principal.allow_download=True
 async def consume():
  response=await public_router.media("share-a","asset-good",request_obj,"child",force_download=True)
  assert response.status_code==200
  assert response.headers["content-disposition"]=="attachment; filename*=UTF-8''summer%20photo.jpg"
  return [chunk async for chunk in response.body_iterator]
 assert asyncio.run(consume())==[b"original"]
