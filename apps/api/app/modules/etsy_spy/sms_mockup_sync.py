"""V4 listing-idea mockup metadata. No marketplace writes, no inferred shade."""
from collections import defaultdict
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

POLICY = "CAM_SMS_MOCKUP_SYNC_V4"
CONTRACT = "CAM_ROLE_01_WORN_04_FOLDED_PROPS_V1"
SCHEMA = "CAM_LISTING_IMAGE_COLOR_V1"
SHA = re.compile(r"[a-f0-9]{64}\Z")
UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
ROLES = {1:"hero",2:"macro",3:"worn_closeup",4:"folded_editorial",
         **{i:"colorway" for i in range(5,13)},13:"infographic"}
SOURCES = {"generation_plan", "verified_muse_plan", "operator_verified"}
CATALOG = Path(__file__).with_name("sms_mockup_palette.json")

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(",",":")).encode()).hexdigest()

@lru_cache(maxsize=1)
def catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))

def _key(value):
    return value.strip().casefold() if isinstance(value,str) else ""

def resolve_color(sku, name):
    """Exact configured names (case-insensitive); no approximate HEX matching."""
    entry = catalog()["garments"].get(sku)
    if entry is None:
        raise ValueError("unknown_garment")
    found = [c for c in entry["colors"] if _key(c["name"]) == _key(name)]
    if len(found) != 1:
        raise ValueError("color_not_configured_for_garment")
    return found[0]

def template_name(sku,name,choices):
    """Match live target spelling. Gildan Sport Grey is the sole explicit alias."""
    color=resolve_color(sku,name)["name"]
    matched=[c for c in choices if _key(c)==_key(color)]
    if not matched and sku in ("G500","G180","G185") and color=="Sport Gray":
        matched=[c for c in choices if _key(c)=="sport grey"]
    if len(matched)!=1:
        raise ValueError("target_color_choice_unresolved")
    return matched[0]

def metadata(*,sku,slot,design_sha,image_sha,color_name,source,contract=CONTRACT):
    if contract!=CONTRACT or type(slot) is not int or slot not in ROLES or source not in SOURCES:
        raise ValueError("unsupported_role_or_source")
    if sku not in catalog()["garments"] or not all(isinstance(s,str) and SHA.fullmatch(s) for s in (design_sha,image_sha)):
        raise ValueError("invalid_image_identity")
    if slot==13:
        if color_name is not None:
            raise ValueError("infographic_has_no_garment_color")
        c={"name":None,"hex":None}
    else:
        c=resolve_color(sku,color_name)
    return {"schema":SCHEMA,"role_contract":CONTRACT,"role":ROLES[slot],"slot":slot,
            "garment_code":sku,"design_sha256":design_sha,"output_sha256":image_sha,
            "color_name":c["name"],"color_hex":c["hex"],"color_source":source,
            "palette_sha256":digest(catalog())}

def input_metadata(value,*,sku,slot,design_sha,image_sha,expected_slots):
    # Old clients may omit the field; never infer colors from slot or filename.
    if value is None:
        return None
    if (expected_slots!=13 or not isinstance(value,dict) or
        set(value)!={"role_contract","color_name","output_sha256","design_sha256"} or
        value["output_sha256"]!=image_sha or value["design_sha256"]!=design_sha):
        raise ValueError("color_receipt_identity_mismatch")
    return metadata(sku=sku,slot=slot,design_sha=design_sha,image_sha=image_sha,
                    color_name=value["color_name"],contract=value["role_contract"],
                    source="generation_plan")

def row_metadata(row):
    raw=getattr(row,"sms_color_metadata",None)
    if not isinstance(raw,dict):
        return None
    try:
        # Keep original source provenance, revalidate against current catalog.
        normalized=metadata(sku=row.garment_code,slot=row.slot,
            design_sha=row.design_sha256,image_sha=row.output_sha256,
            color_name=raw.get("color_name"),source=raw.get("color_source"),
            contract=raw.get("role_contract"))
        if any(raw.get(k)!=normalized[k] for k in ("schema","role","slot","garment_code","design_sha256","output_sha256","color_name")):
            return None
        return normalized
    except (ValueError,TypeError):
        return None

def pack_sha(pack):
    return digest({k:v for k,v in pack.items() if k!="review_sha256"})

def project(rows,master_sha):
    """Expose only role/colour/identity metadata. No Drive IDs or public URLs."""
    groups=defaultdict(list)
    for row in rows:
        groups[(row.garment_code,row.pack_id)].append(row)
    packs=[]
    for (sku,pack),items in sorted(groups.items()):
        slots=sorted(r.slot for r in items)
        consistent=(len(set(slots))==len(items) and all(1<=n<=13 for n in slots)
            and len({(r.design_sha256,r.source_sha256,r.placement) for r in items})==1)
        matched=bool(master_sha and consistent and all(r.design_sha256==master_sha for r in items))
        photos=[]
        for r in sorted(items,key=lambda r:r.slot):
            m=row_metadata(r)
            photos.append({"slot":r.slot,"output_sha256":r.output_sha256,
              "design_sha256":r.design_sha256,"width":r.native_width,"height":r.native_height,
              "preview_only":not isinstance(r.native_width,int) or not isinstance(r.native_height,int)
                             or min(r.native_width,r.native_height)<2000,
              "metadata":m})
        count=sum(p["metadata"] is not None for p in photos)
        full=matched and slots==list(range(1,14)) and count==13
        colors=[p["metadata"]["color_name"] for p in photos if p["metadata"] and p["slot"] in (1,*range(5,13))]
        base=[p["metadata"]["color_name"] for p in photos if p["metadata"] and p["slot"] in range(1,5)]
        coherent=bool(full and len(set(base))==1 and len(colors)==9 and len(set(colors))==9)
        packs.append({"garment_code":sku,"pack_id":pack,"matches_current_master":matched,
            "saved_count":len(items),"color_metadata_count":count,
            "full_v13":full,"palette_coherent":coherent,
            "unique_color_count":len(set(colors)),
            "ready_for_mockup_review":coherent and not any(p["preview_only"] for p in photos),
            "preview_only_count":sum(p["preview_only"] for p in photos),
            "missing_metadata_slots":[p["slot"] for p in photos if p["metadata"] is None],
            "photos":photos})
    from .sms_mockup_incremental import advisory
    for pack in packs:
        pack["incremental"]=advisory(pack)
        pack["review_sha256"]=pack_sha(pack)
    return {"policy":POLICY,"palette_sha256":digest(catalog()),"packs":packs,
            "requires_live_target_color_choices":True,"requires_verified_original_image_links":True,
            "requires_explicit_confirmation":True,"automatic_sms_writes":False}

def plan_chunks(pack, verified_urls, choices, *, target_id, shop):
    """Pure review plan only: full coherent V13 and exact verified ORIGINAL PNG links.
    Caller must independently verify each link's bytes and target snapshot.
    """
    if not pack.get("ready_for_mockup_review") or not pack.get("palette_coherent"):
        raise ValueError("pack_not_ready")
    if not isinstance(choices,list) or not choices or any(not isinstance(c,str) for c in choices):
        raise ValueError("live_target_color_choices_required")
    if not isinstance(target_id,str) or not UUID.fullmatch(target_id) or shop!="Tiktok_McGowanshopdesigns":
        raise ValueError("target_identity_required")
    photos={p["slot"]:p for p in pack["photos"]}
    if set(photos)!=set(range(1,14)):
        raise ValueError("incomplete_v13")
    order=[1,2,3,4,5,6,7,8,13,9,10,11,12]
    urls=[]; colors={}
    for pos,slot in enumerate(order,1):
        p=photos[slot]
        evidence=verified_urls.get(slot)
        if not isinstance(evidence,dict) or evidence.get("output_sha256")!=p["output_sha256"] or evidence.get("verified_original") is not True:
            raise ValueError("original_image_link_unverified")
        url=evidence.get("url")
        if not isinstance(url,str) or not re.fullmatch(r"https://drive\.google\.com/uc\?export=download&id=[A-Za-z0-9_-]{10,128}",url) or url in urls:
            raise ValueError("invalid_or_duplicate_original_link")
        urls.append(url)
        if slot==1 or 5<=slot<=12:
            colors[str(pos)]=template_name(pack["garment_code"],p["metadata"]["color_name"],choices)
    chunks=[]
    for start in range(0,len(urls),6):
        end=min(start+6,len(urls))
        # URL-based assignment avoids confusing global position with batch-local index.
        target={"target_id":target_id,"image_urls":urls[start:end],
                "mockup_colors":{urls[i-1]:c for i,c in ((int(i),c) for i,c in colors.items()) if start<i<=end}}
        if end==len(urls):
            target["gallery"]=9
        chunks.append({"mode":"update","targets":[target],"expected_previous_mockups":start,
                       "expected_after_mockups":end,
                       "expected_colors_after":{i:c for i,c in colors.items() if int(i)<=end},
                       "requires_readback":True})
    return {"policy":POLICY,"shop":shop,"target_id":target_id,"gallery":9,
            "unique_colors":len(set(colors.values())),"ordered_slots":order,
            "expected_colors_by_position":colors,"chunks":chunks,
            "requires_empty_target_mockups":True,"requires_explicit_confirmation":True,
            "publish":False}