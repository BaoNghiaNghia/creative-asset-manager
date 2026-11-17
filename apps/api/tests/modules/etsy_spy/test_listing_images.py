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
    remote_pngs = {}
    revisions = []
    design_sha, source_sha = "c" * 64, "d" * 64
    drive_root = "1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl"
    async def mock_drive(*, tenant_id, pack_id, slot, digest, png,
                         listing_id, design_sha256, placement, garment_code,
                         main_revision_number=0):
        assert (listing_id, design_sha256, placement, garment_code) == (
            "1234567890", design_sha, "front_center", "C1717")
        sends.append((tenant_id, pack_id, slot, digest))
        revisions.append(main_revision_number)
        original_id = f"storedOriginal{main_revision_number}123456"
        thumbnail_id = f"storedPreview{main_revision_number}123456"
        remote_pngs[original_id] = png
        remote_pngs[thumbnail_id] = png
        return {"drive_file_id": original_id,
                "drive_thumbnail_id": thumbnail_id,
                "drive_folder_id": "garmentFolderD12345",
                "drive_thumbnail_folder_id": "thumbnailFolderD12345",
                "thumbnail_sha256": digest}
    async def mock_read(*, tenant_id, asset_id, remote_id, sha256, max_bytes):
        assert tenant_id == "boostgame"
        assert hashlib.sha256(remote_pngs[remote_id]).hexdigest() == sha256
        return remote_pngs[remote_id]
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
                    "mainThumbnailUrl":f"/api/v1/etsy-spy/listing-images/{pack}/1/thumbnail?v={digest[:12]}"}]}
            gallery = client.get(gallery_url).json()
            assert gallery["total"] == 1
            assert gallery["groups"][0]["garmentCode"] == "C1717"
            assert gallery["groups"][0]["images"][0]["designSha256"] == design_sha
            assert gallery["groups"][0]["images"][0]["placement"] == "front_center"
            assert gallery["groups"][0]["images"][0]["manifestReady"] is False
            assert "drive_file_id" not in str(gallery)
            url = gallery["groups"][0]["images"][0]["thumbnailUrl"]
            assert client.get(url).status_code == 200
            changed = io.BytesIO()
            Image.new("RGB", (512, 512), (140, 80, 100)).save(changed, format="PNG")
            fresh_png = changed.getvalue()
            fresh_sha = hashlib.sha256(fresh_png).hexdigest()
            update_payload = {**body, "sha256": fresh_sha,
                "png_base64": base64.b64encode(fresh_png).decode(),
                "replace_slot_sha256": digest, "main_revision_number": 1}
            assert client.post(upload_url,json={**update_payload,"slot":2},
                headers={"Authorization":"Bearer "+secret}).status_code == 422
            assert client.post(upload_url,json={
                **update_payload,"replace_slot_sha256":"e"*64},
                headers={"Authorization":"Bearer "+secret}).status_code == 409
            replaced = client.post(upload_url,json=update_payload,
                headers={"Authorization":"Bearer "+secret})
            assert replaced.status_code == 200, replaced.text
            assert replaced.json()["main_replaced"] is True
            assert replaced.json()["sha256"] == fresh_sha
            assert len(sends) == 2
            assert revisions == [0, 1]
            assert db.get(EtsySpyListingImage, ("boostgame","1234567890",pack,1)).output_sha256 == fresh_sha
            new_gallery = client.get(gallery_url).json()
            assert new_gallery["total"] == 1
            assert new_gallery["groups"][0]["count"] == 1
            assert new_gallery["groups"][0]["images"][0]["sha256"] == fresh_sha
            assert new_gallery["groups"][0]["images"][0]["thumbnailUrl"].endswith(
                f"?v={fresh_sha[:12]}")
            assert client.get(new_gallery["groups"][0]["images"][0]["originalUrl"]).content == fresh_png
            assert client.post(upload_url,json=update_payload,
                headers={"Authorization":"Bearer "+secret}).json()["already_saved"] is True
            assert len(sends) == 2
            assert client.post(upload_url,json=body,
                headers={"Authorization":"Bearer "+secret}).status_code == 409
            # Multiple completed designs can share one Picked row: select
            # the thumbnail SHA from the SAME lexicographic Main pack.
            earlier = "sp-" + "0" * 40
            db.add(EtsySpyListingImage(
                tenant_id="boostgame", listing_key="1234567890",
                pack_id=earlier, slot=1, garment_code="C1717",
                design_sha256="8" * 64, source_sha256="7" * 64,
                placement="front_center", output_sha256="9" * 64,
                thumbnail_sha256="f" * 64,
                drive_folder_id="otherFolderD12345",
                drive_thumbnail_folder_id="otherFolderE12345",
                drive_file_id="otherFileA123456",
                drive_thumbnail_id="otherFileB123456"))
            db.commit()
            multi_badge = listing_badges(db, "boostgame", {"1234567890"})["1234567890"][0]
            assert multi_badge["count"] == 2
            assert multi_badge["mainPackId"] == earlier
            assert multi_badge["mainThumbnailUrl"].endswith(
                f"/{earlier}/1/thumbnail?v={'f'*12}")
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