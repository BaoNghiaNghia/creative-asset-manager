"""Drive adapter contract and real gallery model, fake provider I/O only."""
import asyncio
import hashlib
import sys
import types
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.database import Base
from app.modules.etsy_spy.model import (EtsySpyPicked as Picked,
    EtsySpyListingImage as ListingImage,EtsySpyListingImageDeletion as Deleted)
from app.modules.image_orchestrator import policy as p
from app.modules.image_orchestrator.commands import UploadTicket,gallery_pack_id
from app.modules.image_orchestrator.storage import DriveEvidence,project_gallery,store_central_output

PNG=b"fixture original bytes"
THUMB=b"fixture thumbnail bytes"
SHA=hashlib.sha256(PNG).hexdigest()
THUMB_SHA=hashlib.sha256(THUMB).hexdigest()
PACK="a"*64


def ticket():
    return UploadTicket("storage-test","123456789",PACK,gallery_pack_id(PACK),
        "C1717",4,"attempt-4",1,"b"*64,"c"*64,"d"*64,"front",SHA,1600,1600)


def evidence():
    return DriveEvidence("drive_original_01","drive_thumbnail_01","drive_folder_01",
                         "drive_thumbnail_folder_01",THUMB_SHA)


def receipt():
    return p.StoredReceipt(SHA,1600,1600,evidence().drive_file_id,SHA,"pass",True,"d"*64)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine,tables=[Picked.__table__,ListingImage.__table__,Deleted.__table__])
        self.ticket=ticket()
        with Session(self.engine) as db,db.begin():
            db.add(Picked(tenant_id=self.ticket.tenant,listing_key=self.ticket.listing))

    def tearDown(self):
        self.engine.dispose()

    def test_gallery_projection_idempotent_and_preserves_preview(self):
        t=self.ticket
        with Session(self.engine) as db,db.begin():
            project_gallery(db,t,receipt(),evidence())
        with Session(self.engine) as db,db.begin():
            project_gallery(db,t,receipt(),evidence())
            row=db.get(ListingImage,(t.tenant,t.listing,t.gallery_id,t.role))
            self.assertEqual(row.source_job_id,"cam-image-"+t.pack_id)
            self.assertEqual(row.native_width,1600)
            self.assertEqual(row.output_sha256,SHA)
            self.assertIsNone(row.drive_manifest_id)

    def test_deleted_sha_cannot_reappear(self):
        t=self.ticket
        with Session(self.engine) as db,db.begin():
            db.add(Deleted(tenant_id=t.tenant,listing_key=t.listing,pack_id=t.gallery_id,
                           slot=t.role,output_sha256=SHA))
        with self.assertRaisesRegex(p.Conflict,"gallery_output_deleted"):
            with Session(self.engine) as db,db.begin():
                project_gallery(db,t,receipt(),evidence())
        with Session(self.engine) as db:
            self.assertIsNone(db.get(ListingImage,(t.tenant,t.listing,t.gallery_id,t.role)))

    def test_legacy_alias_collision_and_unpick(self):
        t=self.ticket
        with Session(self.engine) as db,db.begin():
            project_gallery(db,t,receipt(),evidence())
        with Session(self.engine) as db,db.begin():
            db.get(ListingImage,(t.tenant,t.listing,t.gallery_id,t.role)).source_job_id="legacy"
        with self.assertRaisesRegex(p.Conflict,"gallery_pack_alias_conflict"):
            with Session(self.engine) as db,db.begin():
                project_gallery(db,t,receipt(),evidence())
        with Session(self.engine) as db,db.begin():
            db.delete(db.get(Picked,(t.tenant,t.listing)))
        with self.assertRaisesRegex(p.Conflict,"listing_not_picked"):
            with Session(self.engine) as db,db.begin():
                project_gallery(db,t,receipt(),evidence())

    def test_adapter_reuses_drive_and_reads_both_checksums(self):
        calls=[]
        remote={**evidence().__dict__,"native_width":1600,"native_height":1600}
        module=types.ModuleType("app.modules.etsy_spy.imagegen_drive")
        async def store(**kwargs):
            calls.append(("store",kwargs))
            return remote
        async def read(**kwargs):
            calls.append(("read",kwargs))
            return PNG if kwargs["asset_id"].endswith(":original") else THUMB
        module.store_listing_output_to_drive=store
        module.read_design_from_drive=read
        with patch.dict(sys.modules,{"app.modules.etsy_spy.imagegen_drive":module}):
            saved=asyncio.run(store_central_output(self.ticket,PNG))
            self.assertEqual(saved.receipt,receipt())
            self.assertFalse(saved.receipt.publication_ready)
            self.assertEqual(len(calls),3)
            self.assertEqual(calls[0][1]["role_contract"],p.ROLE_CONTRACT)
            self.assertEqual(calls[0][1]["pack_id"],self.ticket.gallery_id)
            self.assertEqual(calls[0][1]["slot"],4)
            with Session(self.engine) as db,db.begin():
                saved.project(db,self.ticket,saved.receipt)
            async def bad_read(**kwargs):
                return b"wrong"
            module.read_design_from_drive=bad_read
            with self.assertRaisesRegex(p.Conflict,"drive_checksum_mismatch"):
                asyncio.run(store_central_output(self.ticket,PNG))
