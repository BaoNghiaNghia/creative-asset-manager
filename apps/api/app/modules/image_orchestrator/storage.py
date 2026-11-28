"""Central image output adapter reusing CAM's existing managed Drive service.

No raw renderer output is retained on VPS. Network I/O is outside database
locks; the command service journals upload before invoking this adapter.
Gallery projection must commit atomically with the role receipt.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import re
from typing import Callable
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import policy as p
from .commands import UploadTicket, gallery_pack_id

_DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{10,128}\Z")


@dataclass(frozen=True)
class SavedOutput:
    receipt: p.StoredReceipt
    project: Callable[[Session,UploadTicket,p.StoredReceipt],None] | None


@dataclass(frozen=True)
class DriveEvidence:
    drive_file_id: str
    drive_thumbnail_id: str
    drive_folder_id: str
    drive_thumbnail_folder_id: str
    thumbnail_sha256: str

    def __post_init__(self):
        p.require(all(isinstance(x,str) and _DRIVE_ID.fullmatch(x) for x in (
            self.drive_file_id,self.drive_thumbnail_id,self.drive_folder_id,
            self.drive_thumbnail_folder_id)), "drive_identity_invalid")
        p.require(p.valid_sha(self.thumbnail_sha256) and
                  self.drive_file_id!=self.drive_thumbnail_id,"drive_identity_invalid")


def project_gallery(db: Session,ticket: UploadTicket,receipt: p.StoredReceipt,
                    evidence: DriveEvidence):
    # No provider requests in this function.
    from app.modules.etsy_spy.model import (
        EtsySpyPicked as Picked,EtsySpyListingImage as ListingImage,
        EtsySpyListingImageDeletion as Deleted)
    p.require(ticket.gallery_id==gallery_pack_id(ticket.pack_id) and
              receipt.image_sha==ticket.image_sha and
              receipt.drive_file_id==evidence.drive_file_id and
              receipt.storage_verified is True and
              ticket.publication_scope==receipt.publication_scope,"gallery_receipt_mismatch")
    if ticket.generation_method is not None:
        p.require(receipt.publication_ready,"native_qa_publication_ineligible")
    picked=db.scalar(select(Picked).where(Picked.tenant_id==ticket.tenant,
                        Picked.listing_key==ticket.listing).with_for_update())
    p.require(picked is not None,"listing_not_picked")
    key=(ticket.tenant,ticket.listing,ticket.gallery_id,ticket.role)
    p.require(db.get(Deleted,(*key,ticket.image_sha)) is None,"gallery_output_deleted")
    source_job="cam-image-"+ticket.pack_id
    # The old gallery uses an sp- alias. Refuse even a theoretical alias
    # collision with a different pack or legacy producer.
    rows=list(db.scalars(select(ListingImage).where(
        ListingImage.tenant_id==ticket.tenant,
        ListingImage.pack_id==ticket.gallery_id).with_for_update()))
    p.require(all(r.listing_key==ticket.listing and r.source_job_id==source_job
                  and r.garment_code==ticket.sku and r.design_sha256==ticket.artwork_sha
                  and r.source_sha256==ticket.source_sha and r.placement==ticket.storage_placement
                  for r in rows),"gallery_pack_alias_conflict")
    previous=next((r for r in rows if r.slot==ticket.role),None)
    if previous is not None:
        p.require(previous.output_sha256==ticket.image_sha and
                  previous.drive_file_id==evidence.drive_file_id and
                  previous.drive_thumbnail_id==evidence.drive_thumbnail_id and
                  previous.thumbnail_sha256==evidence.thumbnail_sha256 and
                  previous.drive_folder_id==evidence.drive_folder_id and
                  previous.drive_thumbnail_folder_id==evidence.drive_thumbnail_folder_id and
                  previous.native_width==ticket.width and previous.native_height==ticket.height,
                  "immutable_gallery_slot")
        return
    db.add(ListingImage(tenant_id=ticket.tenant,listing_key=ticket.listing,
        pack_id=ticket.gallery_id,slot=ticket.role,garment_code=ticket.sku,
        design_sha256=ticket.artwork_sha,source_sha256=ticket.source_sha,
        placement=ticket.storage_placement,drive_folder_id=evidence.drive_folder_id,
        drive_thumbnail_folder_id=evidence.drive_thumbnail_folder_id,
        drive_file_id=evidence.drive_file_id,drive_thumbnail_id=evidence.drive_thumbnail_id,
        output_sha256=ticket.image_sha,thumbnail_sha256=evidence.thumbnail_sha256,
        native_width=ticket.width,native_height=ticket.height,source_job_id=source_job))
    db.flush()


async def store_central_output(ticket: UploadTicket,png: bytes) -> SavedOutput:
    """Call only for a committed, SHA/QA-bound intent and a verified capability."""
    from app.modules.etsy_spy.imagegen_drive import (
        store_listing_output_to_drive,read_design_from_drive)
    p.require(ticket.gallery_id==gallery_pack_id(ticket.pack_id)
              and ticket.storage_placement in ("front","back")
              and hashlib.sha256(png).hexdigest()==ticket.image_sha,
              "upload_identity_mismatch")
    remote=await store_listing_output_to_drive(
        tenant_id=ticket.tenant,pack_id=ticket.gallery_id,slot=ticket.role,
        digest=ticket.image_sha,png=png,listing_id=ticket.listing,
        design_sha256=ticket.artwork_sha,placement=ticket.storage_placement,
        garment_code=ticket.sku,expected_slots=13,role_contract=p.ROLE_CONTRACT)
    p.require((remote.get("native_width"),remote.get("native_height"))==
              (ticket.width,ticket.height),"drive_native_dimensions_mismatch")
    evidence=DriveEvidence(**{key:remote[key] for key in DriveEvidence.__dataclass_fields__})
    # Read back actual bytes, not merely appProperties asserted by a worker.
    # The existing Drive service enforces tenant/asset identity and SHA.
    for label,remote_id,sha,limit in (
        ("original",evidence.drive_file_id,ticket.image_sha,32*1024*1024),
        ("thumbnail",evidence.drive_thumbnail_id,evidence.thumbnail_sha256,2*1024*1024+1)):
        saved=await read_design_from_drive(
            tenant_id=ticket.tenant,asset_id=f"{ticket.gallery_id}:listing-{ticket.role:02d}:{label}",
            remote_id=remote_id,sha256=sha,max_bytes=limit)
        p.require(hashlib.sha256(saved).hexdigest()==sha,"drive_checksum_mismatch")
    receipt=p.StoredReceipt(ticket.image_sha,ticket.width,ticket.height,
        evidence.drive_file_id,ticket.image_sha,"pass",True,ticket.placement_sha,
        *ticket.publication_scope)
    def project(db,t,r):
        p.require(t==ticket and r==receipt,"gallery_projection_identity_mismatch")
        project_gallery(db,t,r,evidence)
    return SavedOutput(receipt,project)
