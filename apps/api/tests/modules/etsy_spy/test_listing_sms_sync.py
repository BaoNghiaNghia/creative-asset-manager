"""Regression coverage for the V2 metadata-only handoff."""
from types import SimpleNamespace
import pytest
from app.modules.etsy_spy.design_artifacts import design_media_path
from app.modules.etsy_spy.listing_sms_sync import (
    ROOT, SHOP, RECEIPT_KEY, audit_media, idea_plan, validate_readback,
)

LISTING = "4380517795"
JOB = "12345678-1234-1234-1234-123456789abc"
ASSET = "a"*24
MASTER = "b"*64
VIDEO = "c"*64
FILE = "exampleVertical123"
URL = "https://drive.google.com/file/d/" + FILE + "/view"
SOURCE = {"title":"Original product", "keyword":"Holiday tee"}
SEO = {"status":"needs_fact_check","job_id":"seo1","result":{
    "title":"Holiday graphic tee","description":"Draft product description",
    "highlights":["one","two","three","four",
                  "Printed and shipped in the USA – no import customs fees"],
    "search_terms":["a","b","c","d","e","f","g","h"]}}
LINK = {"url":URL,"aspect":"9:16","kind":"file","source":"cam_video_ugc_table",
        "storage_verified":True,"sha256":VIDEO}
HORIZONTAL = {**LINK,"url":"https://drive.google.com/file/d/exampleHorizontal123/view",
              "aspect":"16:9","sha256":"d"*64}


def entry(asset=ASSET, digest=MASTER, position="unknown"):
    return {"id":asset,"job_id":JOB,"listing_key":LISTING,"source":"mapped",
            "output_sha256":digest,"position":position,
            "original_url":design_media_path(JOB,asset)}


def raw(asset=ASSET, digest=MASTER):
    return {"id":asset,"output_sha256":digest,"artwork_kind":"native_master_v2",
            "storage_provider":"google_drive_managed","storage_folder_id":ROOT,
            "drive_file_id":"exampleOriginal"+asset[:4],
            "drive_thumbnail_id":"exampleThumb"+asset[:4]}


def extraction(*items):
    return SimpleNamespace(id=JOB,entity_id=LISTING,cancellation_requested=False,
                           payload_json={"design_assets":list(items or (raw(),))})


def muse(**kw):
    d=dict(listing_key=LISTING,artwork_asset_path=design_media_path(JOB,ASSET),
           artwork_sha256=MASTER,product_image_url="https://i.etsystatic.com/shop/photo.jpg")
    return SimpleNamespace(**(d|kw))


def plan(**kw):
    args=dict(listing_key=LISTING,source=SOURCE,seo=SEO,extraction=extraction(),
              designs=[entry()],muse=muse(),table_links=[LINK,HORIZONTAL],saved={})
    return idea_plan(**(args|kw))


def row(slot, digest=MASTER, pack="pack-a", **kw):
    return SimpleNamespace(**(dict(pack_id=pack,slot=slot,garment_code="C1717",
        design_sha256=digest,source_sha256="e"*64,placement="front_center",
        native_width=1254,native_height=1254,drive_manifest_id=None)|kw))


def report(p, **kw):
    return dict(confirm=True,candidate_id=p["candidate_id"],idea_code="I1671",
                idea_id="12345678-1234-1234-1234-123456789abd",
                target_id="12345678-1234-1234-1234-123456789abe",shop=SHOP,
                reference_images=2,target_videos=1,listings_made=0,listings_live=0,
                etsy_id=LISTING,vertical_field_url=URL)|kw


def test_idea_without_template_or_final_pack():
    p=plan()
    assert p["ready_for_idea_review"] and not p["blockers"]
    target=p["payload"]["targets"][0]
    assert target["video_urls"]==[URL] and len(target["highlights"])==4
    assert len(p["payload"]["image_urls"])==2
    assert p["payload"]["image_urls"][1].startswith("https://drive.google.com/thumbnail?id=")
    assert "listing_template" not in target and "price" not in target and "files" not in p["payload"]
    assert len([f for f in p["payload"]["fields"] if "UGC Video" in f["name"]])==2
    assert p["marketplace_publish_allowed"] is False
    assert p["requires_live_duplicate_check"] and p["requires_target_readback"]
    assert p["requires_reference_link_check"]
    assert p["fields_alone_satisfy_target"] is False
    assert p["sms_upload_requested"] is False
    assert "customs fees" not in str(p["payload"])


@pytest.mark.parametrize("kwargs,block",[
    ({"table_links":[HORIZONTAL]}, "verified_vertical_video_missing"),
    ({"table_links":[{**LINK,"source":"operator"}]}, "verified_vertical_video_missing"),
    ({"muse":muse(artwork_sha256="f"*64)}, "video_master_not_current_managed_design"),
    ({"muse":muse(artwork_asset_path=design_media_path(JOB,"f"*24))}, "video_master_not_current_managed_design"),
    ({"muse":muse(product_image_url="https://evil.test/image.jpg")}, "product_reference_missing"),
    ({"extraction":extraction({**raw(),"drive_file_id":"exampleThumbaaaa"})}, "video_master_not_current_managed_design"),
    ({"extraction":extraction({**raw(),"artwork_kind":"preview"})}, "video_master_not_current_managed_design"),
    ({"saved":{"design_sha256":"f"*64}}, "selected_design_differs_from_video"),
    ({"saved":{"shop":"other-shop"}}, "unexpected_shop_for_default_handoff"),
    ({"saved":{"garment_code":"G185"}}, "garment_requires_operator_selection"),
])
def test_fail_closed(kwargs,block):
    p=plan(**kwargs)
    assert not p["ready_for_idea_review"] and p["payload"] is None
    assert block in p["blockers"]


def test_integrity_distinguishes_current_alternate_and_not_current():
    alt="f"*64
    audit=audit_media(extraction(raw(),raw("d"*24,alt)),
        [entry(),entry("d"*24,alt,"back")],muse(),
        [row(2),row(3),row(1,alt,"pack-b"),row(1,"0"*64,"pack-c")])
    assert audit["current_video_master_verified"]
    assert audit["different_from_video_design_count"]==2
    assert audit["other_current_design_pack_count"]==1
    assert audit["noncurrent_design_pack_count"]==1
    assert audit["matching_video_master_has_main"] is False
    assert audit["packs"][0]["expected_slots_known"] is None
    assert audit["packs"][0]["missing_required_slots"]==[]
    assert 1 in audit["packs"][0]["missing_v13_slots_advisory"]
    assert audit["preview_only_count"]==4


def test_unresolved_master_does_not_label_stale():
    a=audit_media(None,[],muse(),[row(1)])
    assert not a["current_video_master_verified"]
    assert a["unresolved_pack_count"]==1 and a["noncurrent_design_pack_count"]==0


def test_known_v13_and_mixed_pack_and_manifest():
    a=audit_media(extraction(),[entry()],muse(),[row(1),row(11),row(1,pack="mixed"),row(2,"f"*64,"mixed")])
    assert a["packs"][1]["expected_slots_known"]==13
    assert 13 in a["packs"][1]["missing_required_slots"]
    assert a["ambiguous_pack_count"]==1
    b=audit_media(extraction(),[entry()],muse(),[row(i,native_width=2000,native_height=2000,drive_manifest_id="manifest") for i in range(1,11)])
    assert b["manifest_ready_count"]==1
    assert b["packs"][0]["expected_slots_known"]==10


def test_readback_count_only_and_stale():
    p=plan()
    receipt=validate_readback(report(p),p)
    assert receipt["target_video_identity_verified"] is False
    assert receipt["live_sms_verified_by_cam"] is False
    assert plan(saved={RECEIPT_KEY:receipt})["readback_state"]=="reported_counts_match"
    assert plan(table_links=[],saved={RECEIPT_KEY:receipt})["readback_state"]=="stale_report"
    assert plan(seo={**SEO,"result":{**SEO["result"],"title":"New title"}},saved={RECEIPT_KEY:receipt})["readback_state"]=="stale_report"
    assert validate_readback(report(p,vertical_field_url=URL+"?resourcekey=0-ExampleResourceKey"),p)["target_videos"]==1


@pytest.mark.parametrize("change",[
    {"target_videos":0},{"reference_images":1},{"confirm":False},{"listings_live":1},
    {"listings_made":1},{"candidate_id":"0"*64},{"etsy_id":"9999999999"},
    {"vertical_field_url":"https://drive.google.com/file/d/exampleWrongFile/view"},
    {"vertical_field_url":"https://drive.google.com/uc?id="+FILE},
    {"target_videos":True},{"shop":"other"},
])
def test_invalid_readback(change):
    p=plan()
    with pytest.raises(ValueError):
        validate_readback(report(p,**change),p)
