"""ImageGen 10-role manifest is emitted only once after ten saved SHA slots."""
from __future__ import annotations
import asyncio
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi import HTTPException

from app.core.database import Base
from app.modules.etsy_spy.model import EtsySpyListingImage as Output
from app.modules.etsy_spy.listing_images import _ensure_pack_manifest


def test_manifest_is_created_once_only_after_all_ten_outputs(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Output.__table__])
    db = Session(engine)
    tenant, listing, pack = "boostgame", "1234567890", "sp-" + "a" * 40
    garment, design, source, placement = "G180", "a"*64, "b"*64, "front_center"
    calls = []
    async def fake_manifest(**args):
        calls.append(args)
        assert args["tenant_id"] == tenant
        assert args["listing_id"] == listing
        assert len(args["images"]) == 10
        assert sorted(x["slot"] for x in args["images"]) == list(range(1,11))
        assert all(x["output_sha256"] == "c"*64 for x in args["images"])
        return "savedManifest123456"
    monkeypatch.setattr(
        "app.modules.etsy_spy.imagegen_drive.store_listing_pack_manifest_to_drive",
        fake_manifest)
    try:
        for slot in range(1, 10):
            db.add(Output(tenant_id=tenant, listing_key=listing, pack_id=pack,
                          slot=slot, garment_code=garment, design_sha256=design,
                          source_sha256=source, placement=placement,
                          drive_folder_id="folderOriginal12345",
                          drive_thumbnail_folder_id="folderThumbnail12345",
                          output_sha256="c"*64, thumbnail_sha256="d"*64,
                          drive_file_id="outputFile1234567",
                          drive_thumbnail_id="thumbFile1234567"))
        db.commit()
        assert asyncio.run(_ensure_pack_manifest(db,tenant,listing,pack,
                           garment,design,source,placement)) is None
        assert not calls, "No manifest should be saved for 9/10 completed images"
        db.add(Output(tenant_id=tenant, listing_key=listing, pack_id=pack,
                      slot=10, garment_code=garment, design_sha256=design,
                      source_sha256=source, placement=placement,
                      drive_folder_id="folderOriginal12345",
                      drive_thumbnail_folder_id="folderThumbnail12345",
                      output_sha256="c"*64, thumbnail_sha256="d"*64,
                      drive_file_id="outputFile1234567",
                      drive_thumbnail_id="thumbFile1234567"))
        db.commit()
        result = asyncio.run(_ensure_pack_manifest(db,tenant,listing,pack,
                             garment,design,source,placement))
        assert result == "savedManifest123456"
        assert len(calls) == 1
        assert db.scalars(select(Output.drive_manifest_id)).all() == [result] * 10
        assert asyncio.run(_ensure_pack_manifest(db,tenant,listing,pack,
                           garment,design,source,placement)) == result
        assert len(calls) == 1, "Idempotent retry must not upload a second manifest"
        try:
            asyncio.run(_ensure_pack_manifest(db,tenant,listing,pack,
                        garment,"f"*64,source,placement))
        except HTTPException as exc:
            assert exc.status_code == 409
        else:
            raise AssertionError("Mismatched immutable design must fail")
    finally:
        db.close()
        engine.dispose()
