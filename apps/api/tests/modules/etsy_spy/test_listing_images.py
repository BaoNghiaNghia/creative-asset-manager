"""ImageGen listing gallery: tenant ACL, idempotent PNG receipts and accurate counts."""
import asyncio
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
from app.modules.etsy_spy.listing_images import (
    listing_image_router, listing_badges, _ensure_pack_manifest,
    listing_preview_requires_review,
)
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
    reads = []
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
        reads.append((asset_id, remote_id))
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
            assert success.json()["preview_only"] is True
            assert client.post(upload_url,json=body,
                headers={"Authorization":"Bearer "+secret}).json()["already_saved"] is True
            assert len(sends) == 1
            assert client.post(upload_url,json={**body,"sha256":"c"*64},
                headers={"Authorization":"Bearer "+secret}).status_code == 409
            assert listing_badges(db,"boostgame",{"1234567890"}) == {
                "1234567890":[{"garmentCode":"C1717","count":1,
                    "reviewCount":1,
                    "mainPackId":pack,
                    "mainThumbnailUrl":f"/api/v1/etsy-spy/listing-images/{pack}/1/thumbnail?v={digest[:12]}"}]}
            gallery = client.get(gallery_url).json()
            assert gallery["total"] == 1
            assert gallery["groups"][0]["garmentCode"] == "C1717"
            assert gallery["groups"][0]["images"][0]["designSha256"] == design_sha
            assert gallery["groups"][0]["images"][0]["placement"] == "front_center"
            assert gallery["groups"][0]["images"][0]["manifestReady"] is False
            assert gallery["groups"][0]["images"][0]["previewOnly"] is True
            assert "drive_file_id" not in str(gallery)
            image = gallery["groups"][0]["images"][0]
            assert "/preview?v=" in image["previewUrl"]
            url = image["thumbnailUrl"]
            first = client.get(url)
            assert first.status_code == 200
            assert first.headers["content-type"].startswith("image/png")
            assert first.headers["cache-control"] == "private, no-cache, must-revalidate"
            assert "Cookie" in first.headers["vary"]
            prior_reads = len(reads)
            cached = client.get(url, headers={"If-None-Match":first.headers["etag"]})
            assert cached.status_code == 304
            assert cached.content == b""
            assert len(reads) == prior_reads
            webp_response = client.get(image["previewUrl"])
            assert webp_response.status_code == 200, webp_response.text
            assert webp_response.headers["content-type"].startswith("image/webp")
            with Image.open(io.BytesIO(webp_response.content)) as thumbnail:
                assert thumbnail.format == "WEBP"
                assert max(thumbnail.size) <= 720
            # Conditional preview response is resolved after the Picked ACL
            # and without any Google Drive I/O.
            prior_reads = len(reads)
            preview_cached = client.get(
                image["previewUrl"],
                headers={"If-None-Match":webp_response.headers["etag"]})
            assert preview_cached.status_code == 304
            assert len(reads) == prior_reads
            original_url = image["originalUrl"]
            original = client.get(original_url)
            assert original.status_code == 200
            assert original.content == png
            prior_reads = len(reads)
            assert client.get(original_url,
                headers={"If-None-Match":original.headers["etag"]}).status_code == 304
            assert len(reads) == prior_reads
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
            # Multiple completed packs share a Picked row. Prefer the
            # newly saved Main even if its ID sorts after the older pack.
            earlier = "sp-" + "f" * 40
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
            assert multi_badge["reviewCount"] == 2
            assert multi_badge["mainPackId"] == earlier
            assert multi_badge["mainThumbnailUrl"].endswith(
                f"/{earlier}/1/thumbnail?v={'f'*12}")
            principal.active_tenant_id = "other-tenant"
            assert client.get(gallery_url).status_code == 404
            assert client.get(url).status_code == 404
            assert client.get(url, headers={"If-None-Match":first.headers["etag"]}).status_code == 404
            assert client.get(image["previewUrl"],
                headers={"If-None-Match":webp_response.headers["etag"]}).status_code == 404
            principal.active_tenant_id = "boostgame"
            db.delete(db.get(EtsySpyPicked, ("boostgame","1234567890")))
            db.commit()
            assert client.get(gallery_url).status_code == 404
            assert client.get(url).status_code == 404
            assert client.get(image["previewUrl"],
                headers={"If-None-Match":webp_response.headers["etag"]}).status_code == 404
    finally:
        db.close()
        engine.dispose()


def test_completed_native_preview_pack_never_claims_final_drive_manifest(monkeypatch):
    assert listing_preview_requires_review(1254, 1254)
    assert listing_preview_requires_review(None, 2000)
    assert not listing_preview_requires_review(2000, 2000)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        EtsySpyAgent.__table__, EtsySpyPicked.__table__,
        EtsySpyListingImage.__table__])
    db = Session(engine)
    pack = "sp-" + "b" * 40
    tenant, listing = "boostgame", "1234567890"
    db.add(EtsySpyPicked(tenant_id=tenant, listing_key=listing))
    for slot in range(1, 11):
        db.add(EtsySpyListingImage(
            tenant_id=tenant, listing_key=listing, pack_id=pack,
            slot=slot, garment_code="C1717",
            design_sha256="c" * 64, source_sha256="d" * 64,
            placement="front_center", output_sha256=f"{slot:064x}",
            thumbnail_sha256="e" * 64,
            drive_folder_id="oneFolderD12345",
            drive_thumbnail_folder_id="thumbFolderD12345",
            drive_file_id=f"remoteOriginal{slot:02d}12345",
            drive_thumbnail_id=f"remoteThumb{slot:02d}12345",
            native_width=1254, native_height=1254))
    db.commit()

    async def must_not_store(**kwargs):
        raise AssertionError("Preview-only ten-role pack must not be finalized")
    monkeypatch.setattr(
        "app.modules.etsy_spy.imagegen_drive.store_listing_pack_manifest_to_drive",
        must_not_store)
    try:
        assert asyncio.run(_ensure_pack_manifest(
            db, tenant, listing, pack, "C1717", "c" * 64,
            "d" * 64, "front_center")) is None
        badges = listing_badges(db, tenant, {listing})[listing]
        assert badges[0]["count"] == 10
        assert badges[0]["reviewCount"] == 10
        assert db.query(EtsySpyListingImage).filter_by(
            drive_manifest_id=None).count() == 10
    finally:
        db.close()
        engine.dispose()


def test_existing_private_r2_cache_only_after_picked_acl(monkeypatch):
    """A fast R2 HIT cannot bypass the tenant/Picked check or serve PNG original."""
    from app.modules.etsy_spy import listing_image_shared_r2 as shared
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
    pack = "sp-" + "b" * 40
    db.add(EtsySpyPicked(tenant_id="boostgame", listing_key="1234567890"))
    db.add(EtsySpyListingImage(
        tenant_id="boostgame", listing_key="1234567890",
        pack_id=pack, slot=1, garment_code="C1717",
        design_sha256="c" * 64, source_sha256="d" * 64,
        placement="front_center", output_sha256="e" * 64,
        thumbnail_sha256="f" * 64,
        drive_folder_id="privateDriveFolder123",
        drive_thumbnail_folder_id="privateThumbnailFolder123",
        drive_file_id="privateDriveFile123",
        drive_thumbnail_id="privateThumbnailFile123"))
    db.commit()
    config = shared.ExistingR2Config(
        bucket="creative-asset-video-cache",
        endpoint="https://example.r2.cloudflarestorage.com",
        access_key="fake", secret_key="fake",
        signing_secret="StrongTestSecret" * 5)
    requests = []
    async def cached_r2(_config, *, tenant_id, pack_id, slot, quality, sha256):
        requests.append((tenant_id, pack_id, quality))
        return b"verified-private-preview"
    async def deny_drive(**kwargs):
        raise AssertionError("Drive must not be called for a private R2 HIT")
    monkeypatch.setattr(shared, "config_for_tenant",
        lambda tenant: config if tenant == "boostgame" else None)
    monkeypatch.setattr(shared, "read_derivative", cached_r2)
    monkeypatch.setattr("app.modules.etsy_spy.imagegen_drive.read_design_from_drive",
                        deny_drive)
    image_url=f"/api/v1/etsy-spy/listing-images/{pack}/1/preview"
    try:
        with TestClient(app) as client:
            response = client.get(image_url)
            assert response.status_code == 200
            assert response.content == b"verified-private-preview"
            assert response.headers["X-CAM-Image-Cache"] == "R2-HIT"
            assert response.headers["content-type"].startswith("image/webp")
            assert requests == [("boostgame", pack, "preview")]
            assert client.get(image_url, headers={
                "If-None-Match": response.headers["ETag"]}).status_code == 304
            assert len(requests) == 1
            principal.active_tenant_id = "another-tenant"
            assert client.get(image_url).status_code == 404
            assert client.get(image_url, headers={
                "If-None-Match": response.headers["ETag"]}).status_code == 404
            assert len(requests) == 1
            principal.active_tenant_id = "boostgame"
            db.delete(db.get(EtsySpyPicked, ("boostgame", "1234567890")))
            db.commit()
            assert client.get(image_url).status_code == 404
            assert len(requests) == 1
    finally:
        db.close()
        engine.dispose()
