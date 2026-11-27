"""Independent C1717 pilot source/plan guard using existing CAM authorities.

Metadata admission is necessary, not sufficient, for execution. The caller
must still verify original bytes, actual calibration evidence, visual QA,
legacy drain, worker capability and shared provider/resource participation.
No video job state or video readiness flag is changed.
"""
from __future__ import annotations

import re
from sqlalchemy import select
from . import policy as p
from . import print_box
from app.modules.etsy_spy.color_match_palette import SMS_C1717_COLORS, ALGORITHM

PALETTE={key:(name,hex_color) for key,name,hex_color,configured in SMS_C1717_COLORS
         if configured and hex_color}
PALETTE_SHA=p.canonical_sha({"algorithm":ALGORITHM,"colors":PALETTE})
_DRIVE=re.compile(r"[A-Za-z0-9_-]{10,128}\Z")


def validate_plan(plan,pack):
    """Validate the pinned server plan; never infer physical measurements."""
    p.require(pack.garment_sku=="C1717","pilot_garment_not_supported")
    p.require(isinstance(plan,dict) and p.canonical_sha(plan)==pack.plan_sha256,
              "image_plan_unverified")
    p.require(plan.get("skillSha256")==pack.skill_sha256==p.SKILL_SHA256 and
              plan.get("garmentSku")==pack.garment_sku and
              plan.get("artworkSha256")==pack.artwork_sha256 and
              plan.get("sourceSha256")==pack.source_sha256 and
              plan.get("storagePlacement")=="front","image_plan_source_mismatch")
    placement = print_box.validate(plan.get("placementLock"),
        artwork_sha=pack.artwork_sha256, placement_sha=pack.placement_sha256,
        mockup_size=plan.get("mockupSize"))
    # This metadata check does not claim to establish a measured 150mm.
    p.require(plan.get("paletteSha256")==PALETTE_SHA,"palette_revision_changed")
    colors=plan.get("roleColors")
    p.require(isinstance(colors,dict) and set(colors)=={f"{n:02d}" for n in range(1,13)}
              and all(isinstance(c,str) and c in PALETTE for c in colors.values()),
              "unconfigured_pilot_color")
    main=colors["01"]
    p.require(all(colors[f"{n:02d}"]==main for n in range(1,5)),
              "base_color_drift")
    alt=[colors[f"{n:02d}"] for n in range(5,13)]
    p.require(len(set(alt))==8 and main not in alt,"duplicate_colorway")
    p.require(plan.get("infographicSwatches")==alt and
              plan.get("infographicSourceRoles")==[1,5,6,7],"infographic_plan_mismatch")
    return {"paletteSha256":PALETTE_SHA,"paletteSource":ALGORITHM,
            "baseColorId":main,"alternateColorIds":alt,
            "placementMetadataVerified":True, **placement}


def validate_selected_asset(selected,rows,pack):
    """Check exact latest saved source descriptor; no image bytes in response."""
    p.require(selected is not None and selected.state=="saved"
              and selected.digest==pack.artwork_sha256 and selected.position=="front",
              "current_artwork_unverified")
    row=next((r for r in rows if r.id==selected.job_id),None)
    p.require(row is not None and row.status=="completed" and
              row.cancellation_requested is False,"current_artwork_unverified")
    payload=row.payload_json if isinstance(row.payload_json,dict) else {}
    assets=payload.get("design_assets")
    matches=[a for a in assets if isinstance(a,dict) and a.get("id")==selected.asset_id
             and a.get("output_sha256")==pack.artwork_sha256] if isinstance(assets,list) else []
    p.require(len(matches)==1,"current_artwork_unverified")
    asset=matches[0]
    p.require(asset.get("artwork_kind")=="native_master_v2" and asset.get("method")=="imagegen"
              and asset.get("position")=="front" and asset.get("position_verified") is True
              and asset.get("source_sha256")==pack.source_sha256
              and asset.get("storage_provider")=="google_drive_managed"
              and isinstance(asset.get("drive_file_id"),str)
              and _DRIVE.fullmatch(asset["drive_file_id"]),"source_asset_unverified")
    return {"jobId":selected.job_id,"assetId":selected.asset_id,
            "artworkSha256":pack.artwork_sha256,"sourceSha256":pack.source_sha256,
            "storageMetadataVerified":True,"originalBytesVerified":False,
            "sourceVisualQaVerified":False}


def inspect_current(db,tenant,pack):
    """Reuse the existing Native Master selection rule, not video queue state."""
    from app.modules.etsy_spy.model import EtsySpyPicked
    from app.modules.processing.model import ProcessingJobModel as Job
    from app.modules.etsy_spy.extraction import EXTRACTION_JOB_TYPE
    from app.modules.muse_bridge.latest_artwork import choose
    p.require(tenant==pack.tenant_id and db.get(EtsySpyPicked,(tenant,pack.listing_key))
              is not None,"listing_not_picked")
    rows=db.execute(select(Job.id,Job.status,Job.cancellation_requested,Job.payload_json).where(
        Job.tenant_id==tenant,Job.entity_type=="etsy_listing",Job.entity_id==pack.listing_key,
        Job.job_type==EXTRACTION_JOB_TYPE,Job.cancellation_requested.is_(False),
        Job.status!="failed").order_by(Job.created_at.desc(),Job.id.desc()).limit(64)).all()
    return validate_selected_asset(choose(rows),rows,pack)