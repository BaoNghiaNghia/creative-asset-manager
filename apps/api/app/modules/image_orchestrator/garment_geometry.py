"""SMS garment metadata, not a render grant or physical calibration.

The operator-captured snapshot preserves the SMS size chart, decoration
bands and reference placement metadata. V3 alone owns the print box.
An SMS reference scale is valid only on its identified reference image;
no extrapolation to a synthetic wearer, other size or other camera.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from . import policy as p, print_box

PROTOCOL="CAM_SMS_GARMENT_GEOMETRY_V1"
SNAPSHOT_SHA256="8c2e8b0245bd172c17848d26cd9c88d84cae76302ec52073e14a0653fced0c62"
SNAPSHOT_PATH=Path(__file__).with_name("sms_garment_geometry.json")
SKUS=("C1717","G500","G180","G185")

def _finite(value, *, positive=False):
    return type(value) in (float,int) and math.isfinite(value) and (value>0 if positive else value>=0)

@lru_cache(maxsize=1)
def _load():
    raw=SNAPSHOT_PATH.read_bytes()
    p.require(hashlib.sha256(raw).hexdigest()==SNAPSHOT_SHA256,"sms_geometry_snapshot_changed")
    data=json.loads(raw)
    p.require(data["schema"]==PROTOCOL and set(data["products"])==set(SKUS)
              and data["sourceUnits"]=={"decoration":"mm","sizeChart":"inches"},
              "sms_geometry_snapshot_invalid")
    return data

def _inch_mm(value):
    # Handles exact supplier fractions such as 26 3/5 and +/- 1 1/4.
    # No guessing dimensions for a size missing in the source chart.
    text=str(value).strip().replace("+/-","").replace("-/+","").strip()
    parts=text.split()
    p.require(1<=len(parts)<=2,"sms_size_chart_invalid")
    try:
        n=sum((Fraction(s) for s in parts),Fraction(0))
    except (ValueError,ZeroDivisionError):
        raise p.Conflict("sms_size_chart_invalid") from None
    p.require(0<n<100,"sms_size_chart_invalid")
    return round(float(n*Fraction(254,10)),4)

def _dimensions(product,size):
    chart=product["sizeChart"]
    cols=chart["columns"]
    p.require(cols[0]=="Measurement" and len(cols)==len(set(cols)),"sms_size_chart_invalid")
    if size not in cols[1:]:
        return {"source":chart["source"],"status":"SIZE_MISSING_FROM_SUPPLIER_CHART",
                "chestWidthMm":None,"bodyLengthMm":None,"chestToleranceMm":None,
                "bodyLengthToleranceMm":None,"nominalOnly":True}
    fields={"Chest Width (Laid Flat)":"chestWidthMm","Body Length":"bodyLengthMm",
            "Chest Tolerance":"chestToleranceMm","Body Length Tolerance":"bodyLengthToleranceMm"}
    result={"source":chart["source"],"status":"SUPPLIER_NOMINAL_DIMENSIONS","nominalOnly":True}
    for label,key in fields.items():
        rows=[row for row in chart["rows"] if row[0]==label]
        p.require(len(rows)<=1 and (not rows or len(rows[0])==len(cols)),"sms_size_chart_invalid")
        result[key]=_inch_mm(rows[0][cols.index(size)]) if rows else None
    return result

def _placement(data,sku,position,size,color,record):
    chosen=None
    if color:
        chosen=data.get("colorReferences",{}).get(sku,{}).get(position,{}).get(color)
    placement=chosen or record.get("placement")
    if placement is None:
        return None
    w,h,scale=(placement.get(k) for k in ("imageWidthPx","imageHeightPx","pixelsPerMm"))
    p.require(type(w) is int and 0<w<=10000 and type(h) is int and 0<h<=10000
              and _finite(scale,positive=True),"sms_reference_invalid")
    p.require(all(_finite(placement.get(k)) and placement[k]<=100 for k in
              ("anchorXPercent","anchorYPercent")),"sms_reference_invalid")
    uri=urlsplit(placement["imageUrl"])
    p.require(uri.scheme=="https" and uri.hostname=="storagesmsprod.blob.core.windows.net"
              and uri.path.startswith("/external-media/") and not uri.username
              and not uri.password,"sms_reference_invalid")
    # Retain no signed URL query or credential. This is a source locator,
    # not a promise that a historical link still works.
    ref={k:v for k,v in placement.items() if k!="imageUrl"}
    ref.update(referenceImageLocator=uri._replace(query="",fragment="").geturl(),
        referenceLinkRefreshRequired=True,referenceImageBytesVerified=False,
        referenceImageSha256=None,referenceColor=color if chosen else None,
        requestedColorMatched=bool(chosen),targetSizeMatchesReference=size==placement["referenceSize"],
        calibrationScope="EXACT_SMS_REFERENCE_IMAGE_ONLY",
        collarLandmarkVerified=False,hemLandmarkVerified=False,
        targetGarmentMappingVerified=False,physicalCalibrationVerified=False,
        smsAnchorIsPlacementAuthority=False)
    return ref

def describe(sku,size,position="Front",color=None):
    data=_load()
    p.require(sku in SKUS,"sms_geometry_sku_unsupported")
    product=data["products"][sku]
    p.require(size in product["sizes"],"sms_geometry_size_unsupported")
    records=[x for x in product["decoration"]["positions"] if x["position"]==position]
    p.require(len(records)==1,"sms_geometry_position_unsupported")
    record=records[0]
    bands=[b for b in record["bands"] if size in b["sizes"]]
    p.require(len(bands)==1,"sms_geometry_band_unavailable")
    band=bands[0]
    width,height=band["maxWidthMm"],band["maxHeightMm"]
    p.require(_finite(width,positive=True) and _finite(height),"sms_geometry_band_invalid")
    ref=_placement(data,sku,position,size,color,record)
    return {"protocol":PROTOCOL,"snapshotDate":data["snapshotDate"],
        "snapshotSha256":SNAPSHOT_SHA256,"source":data["source"],
        "productId":product["productId"],"garmentSku":sku,"productName":product["name"],
        "size":size,"position":position,"requestedColor":color,"method":"Printing 2D",
        "smsDecorationEditedAtUtc":product["decoration"]["lastEditedAtUtc"],
        "supplierDimensions":_dimensions(product,size),
        "productionBand":{"maxWidthMm":width,"maxHeightMm":height,
            "heightStatus":"UNRESOLVED_ZERO" if height==0 else "CONFIGURED_LIMIT",
            "fullProductionFitVerified":False},
        "reference":ref,"referenceStatus":"METADATA_ONLY" if ref else "REFERENCE_NOT_CONFIGURED",
        "placementAuthority":print_box.AUTHORITY,
        "controlledHybridEligible":False,"physicalCalibrationVerified":False,
        "reviewOnly":True,"renderAllowed":False}

def for_plan(product):
    """Attach metadata to a validated, immutable C1717 V3 product plan."""
    lock=product["placementLock"]
    sku=product["garmentSku"]
    p.require(sku=="C1717","sms_geometry_plan_sku_unsupported")
    print_box.validate(lock,artwork_sha=product["artworkSha256"],
                       placement_sha=lock["lock_sha256"],mockup_size=product["mockupSize"])
    shade=product["roleColors"]["01"]
    data=describe(sku,product["mockupSize"],"Front","Black" if shade=="black" else None)
    width=lock["print_width_mm"]
    height=width/lock["ink_aspect_ratio"]
    gap=lock["top_offset_mm"]
    chest=data["supplierDimensions"]["chestWidthMm"]
    band=data["productionBand"]
    p.require(width<=band["maxWidthMm"],"sms_geometry_width_limit_exceeded")
    if band["maxHeightMm"]>0:
        p.require(height<=band["maxHeightMm"],"sms_geometry_height_limit_exceeded")
    ref=data["reference"]
    px=None
    if ref and ref["targetSizeMatchesReference"]:
        scale=ref["pixelsPerMm"]
        px={"widthPx":round(width*scale,4),"heightPx":round(height*scale,4),
            "collarGapPx":round(gap*scale,4),
            "hemClearanceMinPx":round(lock["hem_clearance_min_mm"]*scale,4),
            "scope":"REFERENCE_TEMPLATE_TARGET_ONLY_NOT_MEASUREMENT",
            "absolutePrintBoxAvailable":False,
            "requiresVerifiedLowerCollarAndPanelLandmarks":True}
    data.update(artworkSha256=product["artworkSha256"],
        placementLockSha256=lock["lock_sha256"],lockedPrint={
          "widthMm":width,"heightMm":round(height,4),
          "aspectRatio":lock["ink_aspect_ratio"],"collarGapMm":gap,
          "hemClearanceMinMm":lock["hem_clearance_min_mm"],
          "nominalChestWidthFraction":round(width/chest,6) if chest else None,
          "ratioScope":"SUPPLIER_LAID_FLAT_NOMINAL_NOT_WORN_PIXEL_RATIO",
          "widthWithinBand":True,"fullProductionFitVerified":False},
        referenceTemplateTargets=px,
        blockers=["REFERENCE_IMAGE_BYTES_UNVERIFIED","LOWER_COLLAR_AND_PANEL_UNVERIFIED",
                  "TARGET_GARMENT_MAPPING_UNVERIFIED"]+
                 (["SMS_HEIGHT_LIMIT_UNRESOLVED"] if band["maxHeightMm"]==0 else []),
        newAttemptsAuthorized=False)
    return data

def catalog():
    """No expiring URLs in the index. Query per SKU/position/size."""
    data=_load()
    return {"protocol":PROTOCOL,"snapshotSha256":SNAPSHOT_SHA256,
        "snapshotDate":data["snapshotDate"],"source":data["source"],"reviewOnly":True,
        "renderAllowed":False,"products":[{"garmentSku":sku,"productId":v["productId"],
            "sizes":deepcopy(v["sizes"]),"positions":[r["position"] for r in v["decoration"]["positions"]],
            "supplierChartSizes":v["sizeChart"]["columns"][1:]}
            for sku,v in data["products"].items()]}
