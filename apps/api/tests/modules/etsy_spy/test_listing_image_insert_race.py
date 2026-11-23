"""Concurrent same-slot upload: a lost INSERT race is not an HTTP 500."""
import base64
import hashlib
import io
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.etsy_spy.listing_images import listing_image_router
from app.modules.etsy_spy.model import (
    EtsySpyAgent, EtsySpyPicked, EtsySpyListingImage,
    EtsySpyListingImageDeletion,
)
from app.modules.etsy_spy.service import create_agent


@pytest.mark.parametrize("same_sha", [True, False])
def test_competing_slot_insert_is_idempotent_or_409(monkeypatch, same_sha):
    engine = create_engine("sqlite://",
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        EtsySpyAgent.__table__, EtsySpyPicked.__table__,
        EtsySpyListingImage.__table__,
        EtsySpyListingImageDeletion.__table__,
    ])
    db = Session(engine)
    tenant, listing, pack = "boostgame", "1234567890", "sp-" + "a" * 40
    design_sha, source_sha = "c" * 64, "d" * 64
    try:
        db.add(EtsySpyPicked(tenant_id=tenant, listing_key=listing))
        db.commit()
        agent, token = create_agent(db, tenant, "Concurrent Upload Test")
        out = io.BytesIO()
        Image.new("RGB", (512, 512), (20, 80, 120)).save(out, format="PNG")
        png = out.getvalue()
        output_sha = hashlib.sha256(png).hexdigest()
        winner_sha = output_sha if same_sha else "f" * 64

        async def mock_drive(**_):
            return {
                "drive_file_id": "remoteOriginal123",
                "drive_thumbnail_id": "remoteThumbnail123",
                "drive_folder_id": "remoteFolder123",
                "drive_thumbnail_folder_id": "remoteThumbFolder123",
                "thumbnail_sha256": output_sha,
            }

        monkeypatch.setattr(
            "app.modules.etsy_spy.imagegen_drive.store_listing_output_to_drive",
            mock_drive)
        original_commit = db.commit
        collided = False

        def raced_commit():
            nonlocal collided
            if not collided and any(isinstance(obj, EtsySpyListingImage)
                                    for obj in db.new):
                collided = True
                # The first request lost the race after Drive completed:
                # another transaction's immutable row became authoritative.
                db.rollback()
                db.add(EtsySpyListingImage(
                    tenant_id=tenant, listing_key=listing, pack_id=pack,
                    slot=1, garment_code="C1717",
                    design_sha256=design_sha, source_sha256=source_sha,
                    placement="front_center", output_sha256=winner_sha,
                    thumbnail_sha256="e" * 64,
                    drive_folder_id="remoteFolder123",
                    drive_thumbnail_folder_id="remoteThumbFolder123",
                    drive_file_id="remoteOriginal123",
                    drive_thumbnail_id="remoteThumbnail123",
                    native_width=512, native_height=512))
                original_commit()
                raise IntegrityError(
                    "INSERT INTO etsy_spy_listing_images", {},
                    sqlite3.IntegrityError(
                        "UNIQUE constraint failed: etsy_spy_listing_images.tenant_id"))
            return original_commit()

        db.commit = raced_commit
        app = FastAPI()
        app.include_router(listing_image_router, prefix="/api/v1/etsy-spy")
        app.dependency_overrides[get_db] = lambda: db
        body = {
            "listing_id": listing, "pack_id": pack, "garment_code": "C1717",
            "slot": 1, "sha256": output_sha,
            "design_sha256": design_sha, "source_sha256": source_sha,
            "placement": "front_center",
            "drive_root_folder_id": "1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl",
            "png_base64": base64.b64encode(png).decode("ascii"),
        }
        with TestClient(app) as client:
            result = client.post(
                f"/api/v1/etsy-spy/image-agents/{agent.id}/listing-outputs",
                json=body, headers={"Authorization": "Bearer " + token})
        assert collided is True
        assert result.status_code == (200 if same_sha else 409), result.text
        if same_sha:
            assert result.json()["ok"] is True
            assert result.json()["already_saved"] is True
            assert result.json()["sha256"] == output_sha
        else:
            assert "Immutable ImageGen output slot changed" in result.text
        saved = db.get(EtsySpyListingImage, (tenant, listing, pack, 1))
        assert saved is not None
        assert saved.output_sha256 == winner_sha
        assert db.query(EtsySpyListingImage).count() == 1
    finally:
        db.close()
        engine.dispose()
