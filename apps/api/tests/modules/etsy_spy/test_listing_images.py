"""ImageGen listing gallery: tenant ACL, idempotent PNG receipts and accurate counts."""
import base64
import hashlib
import io
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.etsy_spy.listing_images import listing_image_router, listing_badges
from app.modules.etsy_spy.model import EtsySpyAgent, EtsySpyPicked, EtsySpyListingImage
from app.modules.etsy_spy.service import create_agent


def test_gallery_only_counts_saved_outputs_and_enforces_tenant(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        EtsySpyAgent.__table__, EtsySpyPicked.__table__,
        EtsySpyListingImage.__table__])
    db = Session(engine)
    principal = SimpleNamespace(active_tenant_id="boostgame",
        effective_permissions=frozenset({"etsy_spy.read"}),
        platform_admin=False, navigation_modules=frozenset({"etsy-spy"}))
    app = FastAPI()
    app.include_router(listing_image_router, prefix="/api/v1/etsy-spy")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    db.add(EtsySpyPicked(tenant_id="boostgame", listing_key="1234567890"))
    db.commit()
    agent, secret = create_agent(db, "boostgame", "CAM Image Agent / Sync Test")
    upload_url = f"/api/v1/etsy-spy/image-agents/{agent.id}/listing-outputs"
    gallery_url = "/api/v1/etsy-spy/listings/1234567890/listing-images"
    pack = "sp-" + "a" * 40
    out = io.BytesIO()
    Image.new("RGB", (512, 512), (20, 70, 100)).save(out, format="PNG")
    png = out.getvalue()
    digest = hashlib.sha256(png).hexdigest()
    sends = []
    design_sha, source_sha = "c" * 64, "d" * 64
    drive_root = "1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl"
    async def mock_drive(*, tenant_id, pack_id, slot, digest, png,
                         listing_id, design_sha256, placement, garment_code):
        assert (listing_id, design_sha256, placement, garment_code) == (
            "1234567890", design_sha, "front_center", "C1717")
        sends.append((tenant_id, pack_id, slot, digest))
        return {"drive_file_id": "storedOriginalA123456",
                "drive_thumbnail_id": "storedPreviewB123456",
                "drive_folder_id": "garmentFolderD12345",
                "drive_thumbnail_folder_id": "thumbnailFolderD12345",
                "thumbnail_sha256": "b" * 64}
    async def mock_read(*, tenant_id, asset_id, remote_id, sha256, max_bytes):
        assert tenant_id == "boostgame"
        assert remote_id in {"storedOriginalA123456", "storedPreviewB123456"}
        return png
    monkeypatch.setattr(
        "app.modules.etsy_spy.imagegen_drive.store_listing_output_to_drive",
        mock_drive)
    monkeypatch.setattr(
        "app.modules.etsy_spy.imagegen_drive.read_design_from_drive",
        mock_read)
    body = {"listing_id":"1234567890", "pack_id":pack, "garment_code":"C1717",
            "slot":1, "sha256":digest, "design_sha256":design_sha,
            "source_sha256":source_sha, "placement":"front_center",
            "drive_root_folder_id":drive_root,
            "png_base64":base64.b64encode(png).decode()}
    try:
        with TestClient(app) as client:
            assert client.get(gallery_url).json()["total"] == 0
            assert client.post(upload_url, json=body).status_code == 401
            assert client.post(upload_url, json={**body,"sha256":"c"*64},
                headers={"Authorization":"Bearer "+secret}).status_code == 422
            success = client.post(upload_url,json=body,
                headers={"Authorization":"Bearer "+secret})
            assert success.status_code == 200, success.text
            assert success.json()["already_saved"] is False
            assert client.post(upload_url,json=body,
                headers={"Authorization":"Bearer "+secret}).json()["already_saved"] is True
            assert len(sends) == 1
            assert client.post(upload_url,json={**body,"sha256":"c"*64},
                headers={"Authorization":"Bearer "+secret}).status_code == 409
            assert listing_badges(db,"boostgame",{"1234567890"}) == {
                "1234567890":[{"garmentCode":"C1717","count":1,
                    "mainPackId":pack,
                    "mainThumbnailUrl":f"/api/v1/etsy-spy/listing-images/{pack}/1/thumbnail"}]}
            gallery = client.get(gallery_url).json()
            assert gallery["total"] == 1
            assert gallery["groups"][0]["garmentCode"] == "C1717"
            assert gallery["groups"][0]["images"][0]["designSha256"] == design_sha
            assert gallery["groups"][0]["images"][0]["placement"] == "front_center"
            assert gallery["groups"][0]["images"][0]["manifestReady"] is False
            assert "drive_file_id" not in str(gallery)
            url = gallery["groups"][0]["images"][0]["thumbnailUrl"]
            assert client.get(url).status_code == 200
            principal.active_tenant_id = "other-tenant"
            assert client.get(gallery_url).status_code == 404
            assert client.get(url).status_code == 404
            principal.active_tenant_id = "boostgame"
            db.delete(db.get(EtsySpyPicked, ("boostgame","1234567890")))
            db.commit()
            assert client.get(gallery_url).status_code == 404
            assert client.get(url).status_code == 404
    finally:
        db.close()
        engine.dispose()
