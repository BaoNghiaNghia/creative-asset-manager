"""Exercise real Gallery ORM models and read-only slot projection."""
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from sqlalchemy import create_engine,event
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.core.database import Base
from app.modules.etsy_spy.model import (EtsySpyPicked as Picked,
    EtsySpyListingImage as Image,EtsySpyListingImageDeletion as Deleted)
from app.modules.etsy_spy.slot_coverage import slot_coverage

TENANT="cutover-fixture"
L1="123456789"
L2="123456780"
OTHER="foreign"
PACK="sp-"+"a"*40


def image(tenant=TENANT,listing=L1,slot=1,sha="a"*64,w=1600,h=1600):
    return Image(tenant_id=tenant,listing_key=listing,pack_id=PACK,slot=slot,
        garment_code="C1717",design_sha256="b"*64,source_sha256="c"*64,
        placement="front_center",output_sha256=sha,thumbnail_sha256="d"*64,
        drive_folder_id="fixture_folder",drive_thumbnail_folder_id="fixture_thumb_folder",
        drive_file_id="fixture_original_id",drive_thumbnail_id="fixture_thumbnail_id",
        native_width=w,native_height=h)


def deletion(slot,sha,when,tenant=TENANT,listing=L1):
    return Deleted(tenant_id=tenant,listing_key=listing,pack_id=PACK,slot=slot,
                   output_sha256=sha,deleted_at=when)


class SlotCoverageTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine,tables=[Picked.__table__,Image.__table__,Deleted.__table__])
        with Session(self.engine) as s,s.begin():
            s.add_all([Picked(tenant_id=TENANT,listing_key=L1),
                Picked(tenant_id=TENANT,listing_key=L2),
                Picked(tenant_id=OTHER,listing_key=L1)])
            s.add_all([image(),image(slot=2,sha="e"*64,w=2000,h=2000),
                image(tenant=OTHER,slot=3),image(listing="123456781",slot=4)])
            now=datetime.now(timezone.utc)
            s.add_all([deletion(1,"f"*64,now),
                deletion(5,"1"*64,now-timedelta(days=1)),
                deletion(5,"2"*64,now),
                deletion(6,"3"*64,now,tenant=OTHER),
                deletion(7,"4"*64,now,listing="123456781")])

    def tearDown(self):
        self.engine.dispose()

    def read(self,tenant=TENANT):
        with Session(self.engine) as s:
            return slot_coverage(s,tenant)

    def test_exact_receipt_and_empty_picked_snapshot(self):
        result=self.read()
        self.assertEqual(result["picked_listing_ids"],sorted([L1,L2]))
        self.assertEqual(result["image_total"],2)
        self.assertEqual(result["images"][0]["source_sha256"],"c"*64)
        self.assertTrue(result["images"][0]["preview_only"])
        self.assertFalse(result["images"][1]["preview_only"])
        self.assertFalse(result["qa_verified"])
        self.assertFalse(result["drive_reverified"])
        self.assertNotIn("fixture_original_id",str(result))
        self.assertTrue(result["complete"])

    def test_only_latest_pending_deletion_not_refilled_or_unpicked(self):
        result=self.read()
        self.assertEqual(result["pending_deletions"],[{
            "listing_id":L1,"pack_id":PACK,"slot":5,"deleted_sha256":"2"*64}])
        with Session(self.engine) as s,s.begin():
            s.add(image(slot=5,sha="9"*64))
        self.assertEqual(self.read()["pending_deletions"],[])

    def test_tenant_isolation_and_empty_tenant(self):
        result=self.read(OTHER)
        self.assertEqual(result["image_total"],1)
        self.assertEqual(result["images"][0]["slot"],3)
        self.assertEqual(result["pending_deletions"][0]["slot"],6)
        self.assertEqual(self.read("none")["picked_total"],0)

    def test_bounded_incomplete_never_returns_complete_true(self):
        with patch("app.modules.etsy_spy.slot_coverage.MAX_IMAGES",1):
            with self.assertRaises(HTTPException) as exc:self.read()
            self.assertEqual(exc.exception.status_code,409)
        with patch("app.modules.etsy_spy.slot_coverage.MAX_PICKED",1):
            with self.assertRaises(HTTPException):self.read()

    def test_single_statement_and_malformed_identity_fails_closed(self):
        statements=[]
        def observe(conn,cursor,sql,parameters,context,many):statements.append(sql)
        event.listen(self.engine,"before_cursor_execute",observe)
        self.read()
        event.remove(self.engine,"before_cursor_execute",observe)
        self.assertEqual(len(statements),1)
        with Session(self.engine) as s,s.begin():
            s.get(Image,(TENANT,L1,PACK,1)).source_sha256="unknown"
        with self.assertRaises(HTTPException) as exc:self.read()
        self.assertEqual(exc.exception.status_code,409)
