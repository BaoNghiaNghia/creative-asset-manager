"""V4.1 planner: synthetic media and operator evidence only."""
from types import SimpleNamespace
import pytest
from app.modules.etsy_spy import sms_mockup_sync as v
from app.modules.etsy_spy import sms_mockup_incremental as inc

MASTER="a"*64
IDEA="11111111-1111-1111-1111-111111111111"
TARGET="22222222-2222-2222-2222-222222222222"
OPTS=dict(idea_id=IDEA,target_id=TARGET,shop=inc.SHOP,allow_low_resolution=True)
COLORS=["Bay","Ivory","White","Pepper","Chambray","Blue Jean","Moss","Denim","Blossom"]
def pack(slots=(1,2,3,4),color=False,width=1254,master=MASTER):
    rows=[]
    for i in slots:
        sha=f"{i:064x}"
        meta=v.metadata(sku="C1717",slot=i,design_sha=MASTER,image_sha=sha,
            color_name=None if i==13 else COLORS[0 if i<=4 else i-4],source="generation_plan") if color else None
        rows.append(SimpleNamespace(garment_code="C1717",pack_id="sp-"+"a"*40,slot=i,
            design_sha256=MASTER,source_sha256="b"*64,placement="front",output_sha256=sha,
            native_width=width,native_height=width,sms_color_metadata=meta))
    return v.project(rows,master)["packs"][0]
def links(slots):
    return {i:{"url":f"https://drive.google.com/uc?export=download&id=verifiedOriginal{i:03}",
        "output_sha256":f"{i:064x}","verified_original":True} for i in slots}
def live(count=0,colors=None,choices=(),gallery=None):
    return {"files":[{"position":i,"file":f"sms_{i}.png"} for i in range(1,count+1)],
            "gallery":gallery,"colors":colors,"color_choices":list(choices)}

def test_partial_originals_no_color_choices_and_low_resolution():
    p=pack()
    assert p["incremental"]["can_prepare_incremental"] and not p["ready_for_mockup_review"]
    plan,state=inc.plan_next(p,None,live(),links(range(1,5)),**OPTS)
    assert plan["expected_count"]==4 and plan["expected_gallery"]==4
    assert all(x["below_2000"] for x in plan["additions"])
    t=plan["payload"]["targets"][0]
    assert set(t)=={"target_id","image_urls","gallery"}
    assert not plan["automatic_sms_writes"]
    blocked,_=inc.plan_next(p,None,live(),{},**dict(OPTS,allow_low_resolution=False))
    assert blocked["state"]=="nothing_to_send"
    assert blocked["remaining_slots"]==[1,2,3,4]
    with pytest.raises(ValueError):inc.plan_next(p,None,live(),{},**OPTS)

def test_incremental_reconcile_and_add_metadata_without_duplicate():
    p=pack()
    plan,state=inc.plan_next(p,None,live(),links(range(1,5)),**OPTS)
    state=inc.reserve(state,plan)
    with pytest.raises(ValueError,match="pending_sms_readback"):inc.plan_next(p,state,live(),{},**OPTS)
    with pytest.raises(ValueError,match="readback_mismatch"):inc.record(state,plan["operation_id"],live(3),outcome="applied")
    state=inc.record(state,plan["operation_id"],live(4),outcome="applied")
    assert len(state["received"])==4 and state["last_warnings"]==["gallery_not_confirmed"]
    assert state["sms_image_bytes_verified"] is False
    p=pack(range(1,8),color=True)
    observed=live(4,colors={},choices=COLORS,gallery=4)
    next_plan,_=inc.plan_next(p,state,observed,links(range(5,8)),**OPTS)
    t=next_plan["payload"]["targets"][0]
    assert len(t["image_urls"])==3 and t["mockup_colors"]["1"]=="Bay"
    assert t["gallery"]==7 and next_plan["expected_count"]==7
    changed=live(4);changed["files"][0]["file"]="other.png"
    with pytest.raises(ValueError,match="identity_changed"):inc.plan_next(p,state,changed,{},**OPTS)
    with pytest.raises(ValueError,match="untracked_existing"):inc.plan_next(p,None,observed,{},**OPTS)

def test_full_pack_incremental_max_six_and_gallery_order():
    p=pack(range(1,14),color=True,width=2000)
    state=None;observed=live(colors={},choices=COLORS);expected=[]
    for count in (6,12,13):
        plan,initial=inc.plan_next(p,state,observed,links(range(1,14)),**OPTS)
        assert plan["expected_count"]==count
        expected.extend(x["slot"] for x in plan["additions"])
        state=inc.reserve(initial,plan)
        observed=live(count,colors=plan["expected_colors"],choices=COLORS,gallery=min(count,9))
        state=inc.record(state,plan["operation_id"],observed,outcome="applied")
        assert state["last_warnings"]==[]
    assert expected==list(inc.ORDER) and len(observed["colors"])==9
    plan,_=inc.plan_next(p,state,observed,{},**OPTS)
    assert plan["state"]=="nothing_to_send"

def test_no_overwrite_and_ambiguous_cancel():
    p=pack((1,),color=True)
    plan,state=inc.plan_next(p,None,live(),links([1]),**OPTS)
    state=inc.reserve(state,plan)
    with pytest.raises(ValueError):inc.record(state,plan["operation_id"],live(1),outcome="not_applied")
    cancelled=inc.record(state,plan["operation_id"],live(),outcome="not_applied")
    assert cancelled["pending"] is None
    state=inc.record(state,plan["operation_id"],live(1),outcome="applied")
    observed=live(1,colors={"1":"White"},choices=COLORS,gallery=1)
    next_plan,_=inc.plan_next(p,state,observed,{},**OPTS)
    assert next_plan["state"]=="nothing_to_send"
    assert next_plan["warnings"][0]["reason"]=="existing_color_conflict_keep"

def test_changed_master_and_duplicate_files_block():
    with pytest.raises(ValueError):inc.plan_next(pack(master="c"*64),None,live(),links([1]),**OPTS)
    dup=live(2);dup["files"][1]["file"]=dup["files"][0]["file"]
    with pytest.raises(ValueError):inc.snapshot(dup)


def test_partial_prefix_resumes_only_unsent_photos():
    p=pack(range(1,8),color=True,width=2000)
    plan,state=inc.plan_next(p,None,live(colors={},choices=COLORS),links(range(1,8)),**OPTS)
    state=inc.reserve(state,plan)
    observed=live(3,colors={"1":"Bay"},choices=COLORS,gallery=3)
    state=inc.record(state,plan["operation_id"],observed,outcome="partially_applied")
    assert len(state["received"])==3 and "partial_append_reconciled" in state["last_warnings"]
    next_plan,_=inc.plan_next(p,state,observed,links(range(4,8)),**OPTS)
    assert [r["slot"] for r in next_plan["additions"]]==[4,5,6,7]
    assert next_plan["expected_count"]==7
