"""Synthetic V4 metadata and exact-original link tests; no live writes."""
import asyncio
import hashlib
from types import SimpleNamespace
import httpx
import pytest
from app.modules.etsy_spy import sms_mockup_sync as v
from app.modules.etsy_spy.sms_mockup_share import verify_public_png

SHA="a"*64
TARGET="11111111-1111-1111-1111-111111111111"
SHOP="Tiktok_McGowanshopdesigns"
NAMES=["Bay","Ivory","White","Pepper","Chambray","Blue Jean","Moss","Denim","Blossom"]

def rows():
    return [SimpleNamespace(garment_code="C1717",pack_id="sp-"+"a"*40,slot=i,
        design_sha256=SHA,source_sha256="b"*64,placement="front",output_sha256=f"{i:064x}",
        native_width=2000,native_height=2000,sms_color_metadata=v.metadata(sku="C1717",slot=i,
        design_sha=SHA,image_sha=f"{i:064x}",color_name=None if i==13 else NAMES[0 if i<=4 else i-4],
        source="generation_plan")) for i in range(1,14)]

def pack():
    return v.project(rows(),SHA)["packs"][0]

def links():
    return {i:{"url":f"https://drive.google.com/uc?export=download&id=verifiedOriginal{i:03}",
        "output_sha256":f"{i:064x}","verified_original":True} for i in range(1,14)}

def test_real_catalog_and_aliases():
    assert {sku:len(c["colors"]) for sku,c in v.catalog()["garments"].items()}=={"C1717":56,"G500":61,"G180":38,"G185":42}
    assert v.resolve_color("C1717","  pepper ")["name"]=="Pepper"
    assert v.template_name("G180","Sport Gray",["Sport Grey"])=="Sport Grey"
    with pytest.raises(ValueError):v.resolve_color("C1717","Sport Gray")
    with pytest.raises(ValueError):v.resolve_color("G500","Pepper")
    with pytest.raises(ValueError):v.template_name("C1717","Bay",["White"])

def test_upload_metadata_sha_contract_and_legacy():
    args=dict(sku="C1717",slot=1,design_sha=SHA,image_sha="c"*64,expected_slots=13)
    raw=dict(role_contract=v.CONTRACT,color_name="Bay",output_sha256="c"*64,design_sha256=SHA)
    assert v.input_metadata(None,**args) is None
    assert v.input_metadata(raw,**args)["color_name"]=="Bay"
    for bad in [dict(raw,output_sha256="d"*64),dict(raw,role_contract="old"),dict(raw,color_name="Natural")]:
        with pytest.raises(ValueError):v.input_metadata(bad,**args)
    with pytest.raises(ValueError):v.input_metadata(raw,**dict(args,expected_slots=10))
    with pytest.raises(ValueError):v.metadata(sku="C1717",slot=13,design_sha=SHA,image_sha="c"*64,color_name="Bay",source="generation_plan")

def test_pack_ready_and_fail_closed():
    p=pack()
    assert p["ready_for_mockup_review"] and p["unique_color_count"]==9 and p["review_sha256"]==v.pack_sha(p)
    rr=rows();rr[0].sms_color_metadata=None
    assert v.project(rr,SHA)["packs"][0]["missing_metadata_slots"]==[1]
    rr=rows();rr[0].native_width=1254
    assert not v.project(rr,SHA)["packs"][0]["ready_for_mockup_review"]
    assert not v.project(rows(),"d"*64)["packs"][0]["matches_current_master"]
    rr=rows();rr[4].sms_color_metadata=dict(rr[4].sms_color_metadata,output_sha256="f"*64)
    assert not v.project(rr,SHA)["packs"][0]["ready_for_mockup_review"]
    rr=rows();rr[4].sms_color_metadata=v.metadata(sku="C1717",slot=5,design_sha=SHA,image_sha=rr[4].output_sha256,color_name="Bay",source="generation_plan")
    assert not v.project(rr,SHA)["packs"][0]["palette_coherent"]

def test_exact_order_chunks_and_no_other_fields():
    plan=v.plan_chunks(pack(),links(),NAMES,target_id=TARGET,shop=SHOP)
    assert plan["ordered_slots"]==[1,2,3,4,5,6,7,8,13,9,10,11,12]
    assert plan["gallery"]==9 and plan["unique_colors"]==9 and not plan["publish"]
    assert [len(c["targets"][0]["image_urls"]) for c in plan["chunks"]]==[6,6,1]
    assert [c["expected_after_mockups"] for c in plan["chunks"]]==[6,12,13]
    assert plan["chunks"][-1]["targets"][0]["gallery"]==9
    assert "9" not in plan["expected_colors_by_position"]
    assert plan["expected_colors_by_position"]["1"]=="Bay"
    assert plan["expected_colors_by_position"]["10"]=="Blue Jean"
    for chunk in plan["chunks"]:
        t=chunk["targets"][0];assert set(t)<={"target_id","image_urls","mockup_colors","gallery"}
        assert t["target_id"]==TARGET and chunk["mode"]=="update"
        assert set(t["mockup_colors"])<=set(t["image_urls"])
    for choices in [[],["White"]]:
        with pytest.raises(ValueError):v.plan_chunks(pack(),links(),choices,target_id=TARGET,shop=SHOP)
    bad=links();bad[1]["output_sha256"]="f"*64
    with pytest.raises(ValueError):v.plan_chunks(pack(),bad,NAMES,target_id=TARGET,shop=SHOP)

def test_anonymous_original_png_stream_no_redirect_escape():
    raw=b"\x89PNG\r\n\x1a\n"+b"fixture"
    sha=hashlib.sha256(raw).hexdigest()
    url="https://drive.google.com/uc?export=download&id=verifiedOriginal001"
    async def run():
        def good(request):
            assert "authorization" not in request.headers
            return httpx.Response(200,headers={"content-type":"image/png"},content=raw)
        async with httpx.AsyncClient(transport=httpx.MockTransport(good)) as c:
            assert await verify_public_png(c,url,sha)==len(raw)
            with pytest.raises(ValueError,match="sha_mismatch"):await verify_public_png(c,url,"f"*64)
            with pytest.raises(ValueError,match="size_limit"):await verify_public_png(c,url,sha,8)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={"location":"https://evil.test/image.png"}))) as c:
            with pytest.raises(ValueError,match="unsafe_image_redirect"):await verify_public_png(c,url,sha)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={"content-type":"text/html"},content=b"login"))) as c:
            with pytest.raises(ValueError,match="not_public"):await verify_public_png(c,url,sha)
    asyncio.run(run())
