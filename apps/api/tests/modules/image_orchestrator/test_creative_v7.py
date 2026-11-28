"""V7 read-only planning and routing regression. No live provider or render."""
from copy import deepcopy
from unittest.mock import patch,AsyncMock
import pytest
from app.modules.image_orchestrator import creative_v7 as v7,policy as p,central_read_api as api
from app.modules.image_orchestrator.creative_scenes import THEMES,SCENES,COMPATIBLE
from app.modules.image_orchestrator.identity import ImageIdentity
from test_admission import fixture
import test_read_api

OCCASIONS={"christmas","nativity","halloween","thanksgiving","valentine","easter",
    "mothers_day","fathers_day","st_patricks_day","patriotic","birthday","summer","autumn","winter"}

def profile(theme,risk=True,confidence="clear"):
    return v7.ArtworkProfile(occasion=theme if theme in OCCASIONS else "none",
        niche=theme if theme not in OCCASIONS and theme!="generic_lifestyle" else "generic",
        aesthetic="heritage",confidence=confidence,denseTypography=risk,smallIcons=risk,
        photographicOrDistressed=False)

def args(**extra):
    plan,pack=fixture()
    return dict(listing="1276850250",pack_id="d"*64,plan_sha=pack.plan_sha256,
                product=plan,title="Funny Car Guy Mechanic Tee",**extra)

@pytest.mark.parametrize("theme",list(THEMES))
@pytest.mark.parametrize("risk",[False,True])
def test_diverse_compatible_roles(theme,risk):
    data=args(profile=profile(theme,risk),profile_evidence="e"*64)
    plan=v7.plan_roles(**data)
    assert plan["theme"]==theme and plan["renderAllowed"] is False
    assert plan["plannedDiversity"]["sceneFamilies"]==6
    assert plan["plannedDiversity"]["uniqueWornSignatures"]==10
    assert plan["plannedDiversity"]["styles"]>=3
    assert plan==v7.plan_roles(**data)
    assert len(plan["rolePlans"])==13
    prev=None
    for role in v7.WORN:
        item=plan["rolePlans"][f"{role:02d}"]
        assert item["sceneId"] in THEMES[theme][0].split() and item["sceneId"]!=prev
        prev=item["sceneId"]
        assert item["poseId"] in COMPATIBLE[SCENES[item["sceneId"]][0]]
        if role==3:assert item["poseFamily"]=="standing"
        if risk:
            assert item["method"]=="controlled_hybrid" and item["poseFamily"]!="seated"
            assert "entirely blank" in item["blankScenePrompt"]
            assert data["title"] not in item["blankScenePrompt"]
        else:assert item["method"]=="native_imagegen" and "blankScenePrompt" not in item
    props=plan["rolePlans"]["04"]["props"]
    assert 3<=len(props)<=5 and set(props)<=set(THEMES[theme][1].split("|"))
    assert plan["rolePlans"]["13"]["sourceRoles"]==[1,5,6,7]

def test_preserve_verified_switch_methods_after_native_failures():
    data=args(verified_roles=[2,4],failed_worn_roles=[1,3],profile=profile("automotive",False),profile_evidence="e"*64)
    plan=v7.plan_roles(**data)
    assert plan["verifiedRolesPreserved"]==[2,4] and plan["needsVerifiedGeometry"]
    assert all(plan["rolePlans"][f"{n:02d}"]["method"]=="controlled_hybrid" for n in v7.WORN)
    assert all(plan["rolePlans"][f"{n:02d}"]["status"]=="preserve_verified" for n in (2,4))
    assert plan["productLock"]["placementLock"]["lock_sha256"]==data["product"]["placementLock"]["lock_sha256"]
    assert p.canonical_sha({k:v for k,v in plan.items() if k!="creativePlanSha256"})==plan["creativePlanSha256"]
    assert not plan["newAttemptsAuthorized"] and not plan["physicalCalibrationVerified"]

def test_neutral_fallback_and_artwork_priority():
    a=v7.plan_roles(**args())
    assert a["theme"]=="generic_lifestyle" and a["titleHint"]=="automotive"
    assert a["themeEvidence"]=="title_hint_unverified"
    b=v7.plan_roles(**args(profile=profile("nativity"),profile_evidence="e"*64))
    assert b["theme"]=="nativity" and b["titleHint"]=="automotive"
    c=v7.plan_roles(**args(profile=profile("christmas",confidence="ambiguous"),profile_evidence="e"*64))
    assert c["theme"]=="generic_lifestyle"

def test_product_lock_changes_fail_closed():
    data=args()
    data["product"]["placementLock"]["print_width_mm"]=250
    with pytest.raises(p.Conflict,match="creative_product_plan_changed"):v7.plan_roles(**data)
    data["plan_sha"]=p.canonical_sha(data["product"])
    with pytest.raises(p.Conflict,match="placement_lock_unverified"):v7.plan_roles(**data)

def test_role_specific_qa():
    for role in (2,13):assert "placement_visual" not in v7.qa_policy(role)["hardChecks"]
    assert "correct_neck_tag" in v7.qa_policy(4)["hardChecks"]
    assert "scene_plan_match" in v7.qa_policy(1)["advisoryChecks"]
    assert not v7.qa_policy(1)["allowUnknownAsPass"]

def test_preview_routes_auth_and_no_render_grant():
    from app.modules.image_orchestrator import creative_v7_preview as pv
    f=test_read_api.ReadApiTests();f.setUp()
    try:
        path=f"/api/v1/muse-bridge/jobs/{f.pack.listing}/creative-plan"
        query={"packId":f.pack.pack_id,"sku":"C1717"}
        assert f.client.get(path,params=query).status_code==403
        f.principal=ImageIdentity(test_read_api.TENANT,"muse-image-01","muse")
        assert f.client.get(path,params=query).status_code==503
        expected=v7.plan_roles(**args())
        with patch.object(api,"schema_evidence",return_value={"centralSchemaReady":True}), \
             patch.object(pv,"preview",return_value=expected) as plan, \
             patch.object(pv,"inspect_artwork",new=AsyncMock(return_value=expected)) as inspect:
            result=f.client.get(path,params=query)
            assert result.status_code==200 and result.headers["Cache-Control"]=="private, no-store"
            assert result.json()["renderAllowed"] is False
            plan.assert_called_once()
            assert f.client.post(path+"/inspect",params=query).status_code==200
            inspect.assert_awaited_once()
    finally:f.tearDown()
