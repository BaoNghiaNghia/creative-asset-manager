"""Read-only SMS geometry and placement-authority regression tests."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from app.modules.image_orchestrator import garment_geometry as g,creative_v7 as v7,policy as p,print_box,central_read_api as api
from app.modules.image_orchestrator.identity import ImageIdentity
from test_admission import fixture
import test_read_api

@pytest.mark.parametrize("sku,ref_size,width,scale,chest",[
    ("C1717","M",279.4,0.6817,514.35),
    ("G500","S",254,0.7314,457.2),
    ("G180","L",330.2,0.7488,609.6),
    ("G185","L",330.2,0.8397,609.6)])
def test_exact_sms_reference(sku,ref_size,width,scale,chest):
    d=g.describe(sku,ref_size)
    assert d["productionBand"]["maxWidthMm"]==width
    assert d["productionBand"]["heightStatus"]=="UNRESOLVED_ZERO"
    assert d["supplierDimensions"]["chestWidthMm"]==pytest.approx(chest)
    assert d["reference"]["pixelsPerMm"]==scale
    assert d["reference"]["targetSizeMatchesReference"]
    assert d["reference"]["referenceImageBytesVerified"] is False
    assert d["reference"]["referenceLinkRefreshRequired"]
    assert not d["renderAllowed"] and not d["controlledHybridEligible"]

def test_all_size_position_combinations_and_missing_evidence():
    catalog=g.catalog()
    assert len(catalog["products"])==4
    assert sum(len(v["positions"]) for v in catalog["products"])==11
    for v in catalog["products"]:
        for size in v["sizes"]:
            for position in v["positions"]:
                d=g.describe(v["garmentSku"],size,position)
                assert not d["physicalCalibrationVerified"]
                assert not d["productionBand"]["fullProductionFitVerified"]
    xs=g.describe("G500","XS")
    assert xs["supplierDimensions"]["chestWidthMm"] is None
    assert xs["supplierDimensions"]["status"]=="SIZE_MISSING_FROM_SUPPLIER_CHART"
    assert not xs["reference"]["targetSizeMatchesReference"]
    assert g.describe("G500","M","Back")["reference"] is None
    sleeve=g.describe("G185","M","Left Sleeve")
    assert sleeve["productionBand"]["maxWidthMm"]==101.6
    assert sleeve["productionBand"]["maxHeightMm"]==457.2
    assert not sleeve["controlledHybridEligible"]

def current_fixture(size="M"):
    plan,_=fixture()
    plan["mockupSize"]=size
    plan["placementLock"].update(print_width_mm=190,width_source="explicit_design_width",ink_aspect_ratio=0.92)
    plan["placementLock"]["lock_sha256"]=print_box.lock_digest(plan["placementLock"])
    colors=list(plan["roleColors"].values())
    from app.modules.image_orchestrator.admission import PALETTE
    alt=[x for x in PALETTE if x!="black"][:8]
    plan["roleColors"]={**{f"{n:02d}":"black" for n in range(1,5)},
                       **{f"{n:02d}":alt[n-5] for n in range(5,13)}}
    plan["infographicSwatches"]=alt
    return plan

def test_pack_bound_targets_without_override():
    plan=current_fixture()
    original=deepcopy(plan)
    d=g.for_plan(plan)
    assert plan==original
    assert d["lockedPrint"]["widthMm"]==190
    assert d["lockedPrint"]["heightMm"]==pytest.approx(206.5217)
    assert d["lockedPrint"]["collarGapMm"]==150
    assert d["lockedPrint"]["nominalChestWidthFraction"]==pytest.approx(190/514.35,abs=1e-6)
    assert d["referenceTemplateTargets"]["widthPx"]==pytest.approx(129.523)
    assert d["referenceTemplateTargets"]["collarGapPx"]==pytest.approx(102.255)
    assert not d["referenceTemplateTargets"]["absolutePrintBoxAvailable"]
    assert d["reference"]["referenceColor"]=="Black"
    assert not d["reference"]["smsAnchorIsPlacementAuthority"]
    assert d["placementLockSha256"]==plan["placementLock"]["lock_sha256"]
    assert "SMS_HEIGHT_LIMIT_UNRESOLVED" in d["blockers"]
    assert not d["newAttemptsAuthorized"]
    assert g.for_plan(current_fixture("L"))["referenceTemplateTargets"] is None

def test_no_arbitrary_reference_substitution():
    d=g.describe("C1717","M","Front","Pepper")
    assert not d["reference"]["requestedColorMatched"]
    assert d["reference"]["referenceColor"] is None
    with pytest.raises(p.Conflict):g.describe("G185","M","Left Chest")
    with pytest.raises(p.Conflict):g.describe("C1717","XS")

def test_creative_plan_binds_geometry_and_keeps_product():
    plan=current_fixture()
    result=v7.plan_roles(listing="1276850250",pack_id="d"*64,
        plan_sha=p.canonical_sha(plan),product=plan,verified_roles=[2,4],
        failed_worn_roles=[1,3])
    assert result["garmentGeometry"]==g.for_plan(plan)
    assert result["productLock"]["placementLock"]==plan["placementLock"]
    assert result["verifiedRolesPreserved"]==[2,4]
    assert not result["renderAllowed"]
    assert p.canonical_sha({k:v for k,v in result.items() if k!="creativePlanSha256"})==result["creativePlanSha256"]

def test_routes_fail_closed_and_return_metadata():
    f=test_read_api.ReadApiTests();f.setUp()
    try:
        prefix="/api/v1/muse-bridge/garment-geometry"
        params={"sku":"C1717","size":"M","position":"Front","color":"Black"}
        assert f.client.get(prefix,params=params).status_code==403
        f.principal=ImageIdentity(test_read_api.TENANT,"muse-image-01","muse")
        with patch.object(api,"schema_evidence",return_value={"centralSchemaReady":True}):
            r=f.client.get(prefix,params=params)
            assert r.status_code==200
            assert r.headers["Cache-Control"]=="private, no-store"
            assert r.json()["reference"]["referenceColor"]=="Black"
            assert not r.json()["renderAllowed"]
            assert len(f.client.get(prefix+"/catalog").json()["products"])==4
            assert f.client.get(prefix,params={**params,"size":"XS"}).status_code==409
            assert f.client.get(prefix,params={**params,"sku":"unknown"}).status_code==422
    finally:f.tearDown()
