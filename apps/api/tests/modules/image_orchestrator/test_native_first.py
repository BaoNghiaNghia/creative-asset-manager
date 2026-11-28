"""Offline regression for internal Native-first policy (not live provider QA)."""
from dataclasses import replace
import pytest
from app.modules.image_orchestrator import native_first as n, policy as p

SHA="a"*64
ID=p.PackIdentity("tenant","4603662026","b"*64,"C1717",1,"c"*64,"d"*64)
PROFILE=n.Profile(SHA,1600,True,True,"e"*64)
CHECKS={k:True for k in n.CHECKS}

def review(profile=PROFILE, **overrides):
    args=dict(role=1,epoch=1,attempt_id="attempt-1",plan_sha="f"*64,
              method=n.NATIVE,image_sha="1"*64,reviewed_sha="1"*64,width=1600,
              height=1600,native_verified=True,upscaled=False,checks=CHECKS,
              reviewer="cam-auto-review",worker="muse-image-01",evidence_sha="2"*64,
              prompt_sha=SHA,physical_calibration=False)
    return n.assess(profile,ID,**{**args,**overrides})

def fail(i=1, method=n.NATIVE, failure="FAIL_ARTWORK_REDRAWN",state="confirmed_failure"):
    return dict(attemptId=f"a-{i}",method=method,state=state,failure=failure)

def test_defaults_and_explicit_policy():
    assert n.Profile(SHA).minimum_px==2000
    assert n.Profile(SHA).visual_qa is False
    for kw in (dict(minimum_px=1600),dict(visual_qa=True),dict(hybrid_fallback=True)):
        with pytest.raises(p.Conflict,match="operator_policy_required"):
            n.Profile(SHA,**kw)

def test_native_1600_eligible_not_published():
    r=review()
    assert r["eligibleForStorageAdmission"] and not r["previewOnly"]
    assert not r["physicalCalibrationVerified"] and not r["publicationAuthorized"]
    h=r.pop("qaRecordSha256")
    assert h==p.canonical_sha(r)

def test_strict_profile_and_hybrid_preview():
    assert review(replace(PROFILE,minimum_px=2000))["previewOnly"]
    assert not review(method=n.HYBRID)["eligibleForStorageAdmission"]
    assert review(replace(PROFILE,minimum_px=2000),width=2000,height=2000)["eligibleForStorageAdmission"]

@pytest.mark.parametrize("kw,reason",[
    ({"width":1599,"height":1599},"dimensions_invalid"),
    ({"height":1601},"dimensions_invalid"),
    ({"upscaled":True},"origin_or_calibration_invalid"),
    ({"native_verified":False},"origin_or_calibration_invalid"),
    ({"physical_calibration":True},"origin_or_calibration_invalid"),
    ({"reviewed_sha":"0"*64},"identity_mismatch"),
    ({"prompt_sha":"0"*64},"identity_mismatch"),
    ({"reviewer":"muse-image-01"},"self_approval_denied"),
    ({"checks":{}},"checks_invalid"),
    ({"checks":{**CHECKS,"roleFraming":"true"}},"checks_invalid"),
])
def test_bad_qa(kw,reason):
    with pytest.raises(p.Conflict,match=reason):
        review(**kw)

def test_visual_only_and_failed_check():
    with pytest.raises(p.Conflict,match="visual_policy_not_approved"):
        review(n.Profile(SHA))
    r=review(checks={**CHECKS,"artworkFidelityAndReadability":False})
    assert not r["qaPass"] and not r["eligibleForStorageAdmission"]

def test_method_router():
    assert n.next_method(PROFILE,1,[])==n.NATIVE
    assert n.next_method(PROFILE,1,[fail()])==n.NATIVE
    assert n.next_method(PROFILE,1,[fail()],hybrid_adapter_verified=True)==n.HYBRID
    assert n.next_method(PROFILE,2,[fail()],hybrid_adapter_verified=True)==n.NATIVE
    assert n.next_method(PROFILE,1,[fail(failure="FAIL_LIGHTING")],hybrid_adapter_verified=True)==n.NATIVE
    assert n.next_method(PROFILE,1,[fail(),fail(2,n.HYBRID)],hybrid_adapter_verified=True)==n.NATIVE

@pytest.mark.parametrize("history,reason",[
    ([fail(state="dispatch_uncertain")],"reconcile"),
    ([fail(state="verified")],"preserve"),
    ([fail(),fail()],"history_invalid"),
    ([fail(method=n.HYBRID)],"hybrid_history_invalid"),
    ([fail(),fail(2),fail(3)],"attempt_limit"),
    ([fail(),fail(2,n.HYBRID),fail(3,n.HYBRID)],"hybrid_history_invalid"),
])
def test_attempt_guards(history,reason):
    with pytest.raises(p.Conflict,match=reason):
        n.next_method(PROFILE,1,history,hybrid_adapter_verified=True)

def test_receipt_digest_changes_for_role_attempt_and_method():
    base=review()["qaRecordSha256"]
    assert review(role=2)["qaRecordSha256"]!=base
    assert review(epoch=2)["qaRecordSha256"]!=base
    assert review(attempt_id="attempt-2")["qaRecordSha256"]!=base
    assert review(method=n.HYBRID)["qaRecordSha256"]!=base
