"""One-photo CAM+Drive delete: tenant ACL, exact SHA, retry, no old replay."""
import io
import base64
import hashlib
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.etsy_spy.listing_images import listing_image_router, listing_badges
from app.modules.etsy_spy.model import (
    EtsySpyAgent, EtsySpyPicked, EtsySpyListingImage,
    EtsySpyListingImageDeletion,
)
from app.modules.etsy_spy.service import create_agent


def _setup():
    engine = create_engine("sqlite://",
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        EtsySpyAgent.__table__, EtsySpyPicked.__table__,
        EtsySpyListingImage.__table__, EtsySpyListingImageDeletion.__table__,
    ])
    db = Session(engine)
    user = SimpleNamespace(active_tenant_id="boostgame",
        effective_permissions=frozenset({"etsy_spy.read", "etsy_spy.manage"}),
        platform_admin=False, navigation_modules=frozenset({"etsy-spy"}))
    app = FastAPI()
    app.include_router(listing_image_router, prefix="/api/v1/etsy-spy")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_authenticated_principal] = lambda: user
    db.add(EtsySpyPicked(tenant_id="boostgame", listing_key="1234567890"))
    db.commit()
    agent, token = create_agent(db, "boostgame", "Delete Test Agent")
    return engine, db, user, app, agent, token


def _row(*, slot, sha, pack=None, manifest=None):
    return EtsySpyListingImage(
        tenant_id="boostgame", listing_key="1234567890",
        pack_id=pack or "sp-" + "a" * 40, slot=slot,
        garment_code="C1717", design_sha256="b" * 64,
        source_sha256="c" * 64, placement="front_center",
        drive_folder_id="garmentFolderA12345",
        drive_thumbnail_folder_id="thumbnailFolderA12345",
        drive_manifest_id=manifest,
        output_sha256=sha,
        thumbnail_sha256="f" * 64,
        drive_file_id=f"originalPhoto{slot}1234567",
        drive_thumbnail_id=f"thumbnailPhoto{slot}1234567",
        native_width=2000, native_height=2000,
    )


def _delete_url(pack="sp-" + "a" * 40, slot=1):
    return "/api/v1/etsy-spy/listings/1234567890/listing-images/" + pack + "/" + str(slot)


def test_delete_exact_selected_photo_and_drive_preserves_other_roles(monkeypatch):
    from app.modules.etsy_spy import imagegen_drive
    engine, db, user, app, agent, token = _setup()
    pack = "sp-" + "a" * 40
    db.add_all([
        _row(slot=1, sha="d" * 64, manifest="manifestFileA12345"),
        _row(slot=2, sha="e" * 64, manifest="manifestFileA12345"),
    ])
    db.commit()
    removed = []
    async def fake_drive(**args):
        removed.append(args)
    monkeypatch.setattr(imagegen_drive, "delete_listing_drive_object", fake_drive)
    try:
        with TestClient(app) as client:
            url = _delete_url()
            request = {"sha256": "d" * 64, "confirm": True}
            user.effective_permissions = frozenset({"etsy_spy.read"})
            assert client.request("DELETE", url, json=request).status_code == 403
            user.effective_permissions = frozenset({"etsy_spy.read", "etsy_spy.manage"})
            assert client.request("DELETE", url, json={
                "sha256":"d" * 64, "confirm": False}).status_code == 422
            assert client.request("DELETE", url, json={
                "sha256":"0" * 64, "confirm": True}).status_code == 409
            user.active_tenant_id = "other-tenant"
            assert client.request("DELETE", url, json=request).status_code == 404
            user.active_tenant_id = "boostgame"
            assert removed == []
            first = client.request("DELETE", url, json=request)
            assert first.status_code == 200, first.text
            assert first.json()["regenerationStatus"] == "pending"
            assert [item["kind"] for item in removed] == [
                "manifest", "thumbnail", "original"]
            assert [item["remote_file_id"] for item in removed] == [
                "manifestFileA12345", "thumbnailPhoto11234567",
                "originalPhoto11234567"]
            assert all(item["tenant_id"] == "boostgame" for item in removed)
            remaining = db.get(EtsySpyListingImage, ("boostgame",
                "1234567890", pack, 2))
            assert remaining is not None
            assert remaining.output_sha256 == "e" * 64
            assert remaining.drive_manifest_id is None
            assert db.get(EtsySpyListingImage, ("boostgame",
                "1234567890", pack, 1)) is None
            assert client.get("/api/v1/etsy-spy/listings/1234567890/listing-images").json()["total"] == 1
            assert listing_badges(db, "boostgame", {"1234567890"})[
                "1234567890"][0]["count"] == 1
            assert client.get("/api/v1/etsy-spy/listing-images/" +
                              pack + "/1/thumbnail").status_code == 404
            receipt = db.get(EtsySpyListingImageDeletion, (
                "boostgame", "1234567890", pack, 1, "d" * 64))
            assert receipt.state == "deleted"
            assert receipt.regeneration_status == "pending"
            assert all([receipt.manifest_deleted,
                        receipt.thumbnail_deleted, receipt.original_deleted])
            retry = client.request("DELETE", url, json=request)
            assert retry.status_code == 200 and retry.json()["already_deleted"]
            assert len(removed) == 3
            backlog_url = ("/api/v1/etsy-spy/image-agents/" + agent.id +
                           "/listing-image-regeneration-requests")
            assert client.get(backlog_url).status_code == 401
            backlog = client.get(backlog_url, headers={
                "Authorization": "Bearer " + token})
            assert backlog.status_code == 200
            assert backlog.json()["autoGenerate"] is False
            assert backlog.json()["total"] == 1
            assert backlog.json()["requests"][0]["slot"] == 1
            assert "drive_file_id" not in str(backlog.json())
            raw = io.BytesIO()
            Image.new("RGB", (512, 512), (30, 60, 90)).save(raw, format="PNG")
            body = {"listing_id": "1234567890", "pack_id": pack,
                "garment_code": "C1717", "slot": 1,
                "sha256": "d" * 64, "design_sha256": "b" * 64,
                "source_sha256": "c" * 64, "placement": "front_center",
                "drive_root_folder_id": "1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl",
                "png_base64": base64.b64encode(raw.getvalue()).decode()}
            assert client.post("/api/v1/etsy-spy/image-agents/" +
                agent.id + "/listing-outputs", json=body,
                headers={"Authorization": "Bearer " + token}).status_code == 410
            assert len(removed) == 3
    finally:
        db.close()
        engine.dispose()


def test_partial_drive_failure_resumes_from_verified_receipt_not_redelete(monkeypatch):
    from app.modules.etsy_spy import imagegen_drive
    engine, db, user, app, _agent, _token = _setup()
    db.add(_row(slot=1, sha="d" * 64, manifest="manifestFileA12345"))
    db.commit()
    calls = []
    fails = {"remaining": 1}
    async def flaky(**args):
        calls.append(args["kind"])
        if args["kind"] == "thumbnail" and fails["remaining"]:
            fails["remaining"] -= 1
            raise HTTPException(503, "Drive thumbnail temporarily unavailable")
    monkeypatch.setattr(imagegen_drive, "delete_listing_drive_object", flaky)
    try:
        with TestClient(app) as client:
            request = {"sha256": "d" * 64, "confirm": True}
            first = client.request("DELETE", _delete_url(), json=request)
            assert first.status_code == 503
            assert db.get(EtsySpyListingImage, ("boostgame",
                "1234567890", "sp-" + "a" * 40, 1)) is not None
            receipt = db.get(EtsySpyListingImageDeletion, (
                "boostgame","1234567890","sp-"+"a"*40,1,"d"*64))
            assert receipt.state == "pending" and receipt.manifest_deleted
            assert not receipt.thumbnail_deleted and not receipt.original_deleted
            second = client.request("DELETE", _delete_url(), json=request)
            assert second.status_code == 200, second.text
            assert calls == ["manifest", "thumbnail", "thumbnail", "original"]
            assert client.get("/api/v1/etsy-spy/listings/1234567890/listing-images").json()["total"] == 0
    finally:
        db.close()
        engine.dispose()
