"""V4.1 append-only mockup planner and operator readback. No SMS writes."""
import re
from .sms_mockup_sync import SHA, UUID, digest, template_name, pack_sha

POLICY="CAM_SMS_MOCKUP_SYNC_V41"
KEY="_sms_mockups_v41_progress"
SHOP="Tiktok_McGowanshopdesigns"
ORDER=(1,2,3,4,5,6,7,8,13,9,10,11,12)
URL=re.compile(r"https://drive\.google\.com/uc\?export=download&id=[A-Za-z0-9_-]{10,128}\Z")

def snapshot(v):
    if not isinstance(v,dict) or set(v)!={"files","gallery","colors","color_choices"}:
        raise ValueError("invalid_sms_snapshot")
    files=v["files"]
    if not isinstance(files,list) or len(files)>13:
        raise ValueError("invalid_sms_file_count")
    seen=set()
    for pos,row in enumerate(files,1):
        if (not isinstance(row,dict) or set(row)!={"position","file"} or
            type(row["position"]) is not int or row["position"]!=pos or
            not isinstance(row["file"],str) or not 1<=len(row["file"])<=300 or
            any(ord(c)<32 for c in row["file"]) or row["file"] in seen):
            raise ValueError("ambiguous_sms_file_identity")
        seen.add(row["file"])
    g=v["gallery"]
    if g is not None and (type(g) is not int or not 1<=g<=min(9,len(files))):
        raise ValueError("invalid_sms_gallery")
    choices=v["color_choices"]
    if (not isinstance(choices,list) or len(choices)>100 or
        any(not isinstance(c,str) or not 1<=len(c)<=64 for c in choices) or
        len({c.casefold() for c in choices})!=len(choices)):
        raise ValueError("invalid_sms_color_choices")
    colors=v["colors"]
    if colors is not None and (not isinstance(colors,dict) or
        any(k not in {str(n) for n in range(1,len(files)+1)} or
            not isinstance(c,str) or not 1<=len(c)<=64 for k,c in colors.items())):
        raise ValueError("invalid_sms_color_map")
    return v

def eligible(pack):
    p=pack.get("photos",[])
    return bool(pack.get("matches_current_master") and p and
        len({x["slot"] for x in p})==len(p) and
        len({x["output_sha256"] for x in p})==len(p) and
        all(x["slot"] in ORDER and isinstance(x["output_sha256"],str) and
            SHA.fullmatch(x["output_sha256"]) and
            type(x["width"]) is int and type(x["height"]) is int and
            x["width"]>0 and x["height"]>0 for x in p))

def advisory(pack):
    return {"policy":POLICY,"can_prepare_incremental":eligible(pack),
            "available_slots":[p["slot"] for p in pack["photos"]],
            "below_2000_slots":[p["slot"] for p in pack["photos"] if p["preview_only"]],
            "color_unverified_slots":[p["slot"] for p in pack["photos"]
                if p["slot"] in (1,*range(5,13)) and not p["metadata"]],
            "requires_original_sha_verification":True}

def check(pack,progress,live,idea_id,target_id,shop):
    if (not eligible(pack) or shop!=SHOP or not isinstance(idea_id,str) or not UUID.fullmatch(idea_id)
        or not isinstance(target_id,str) or not UUID.fullmatch(target_id)):
        raise ValueError("invalid_incremental_pack_or_target")
    live=snapshot(live)
    identity={"policy":POLICY,"idea_id":idea_id,"target_id":target_id,
              "shop":shop,"pack_id":pack["pack_id"],"garment_code":pack["garment_code"],
              "design_sha256":pack["photos"][0]["design_sha256"]}
    if progress is None:
        if live["files"]:
            raise ValueError("untracked_existing_sms_mockups")
        progress={**identity,"received":[],"pending":None}
    elif not isinstance(progress,dict) or any(progress.get(k)!=v for k,v in identity.items()):
        raise ValueError("existing_mockup_identity_changed")
    if progress.get("pending"):
        raise ValueError("pending_sms_readback_reconcile_first")
    received=progress["received"]
    photos={p["slot"]:p for p in pack["photos"]}
    if len(received)!=len(live["files"]) or len({r["slot"] for r in received})!=len(received):
        raise ValueError("sms_picture_count_changed")
    for pos,(row,file) in enumerate(zip(received,live["files"]),1):
        p=photos.get(row["slot"])
        if (row["position"]!=pos or row["sms_file"]!=file["file"] or p is None
            or row["output_sha256"]!=p["output_sha256"]):
            raise ValueError("sms_picture_identity_changed")
    return progress

def pending_slots(pack,progress,live,*,idea_id,target_id,shop,allow_low_resolution):
    progress=check(pack,progress,live,idea_id,target_id,shop)
    received={x["slot"] for x in progress["received"]}
    photos={p["slot"]:p for p in pack["photos"]}
    return [n for n in ORDER if n in photos and n not in received
            and (allow_low_resolution or not photos[n]["preview_only"])][:6]

def plan_next(pack,progress,live,verified_urls,*,idea_id,target_id,shop,allow_low_resolution=False):
    progress=check(pack,progress,live,idea_id,target_id,shop)
    slots=pending_slots(pack,progress,live,idea_id=idea_id,target_id=target_id,shop=shop,
                        allow_low_resolution=allow_low_resolution)
    photos={p["slot"]:p for p in pack["photos"]}
    previous=progress["received"]
    changes={"target_id":target_id}
    additions=[]
    used=set()
    for n in slots:
        ev=verified_urls.get(n)
        if (not isinstance(ev,dict) or ev.get("verified_original") is not True
            or ev.get("output_sha256")!=photos[n]["output_sha256"] or
            not isinstance(ev.get("url"),str) or not URL.fullmatch(ev["url"]) or ev["url"] in used):
            raise ValueError("original_image_link_unverified")
        used.add(ev["url"])
        additions.append({"slot":n,"position":len(previous)+len(additions)+1,
            "output_sha256":photos[n]["output_sha256"],"url":ev["url"],
            "below_2000":bool(photos[n]["preview_only"])})
    if additions:
        changes["image_urls"]=[r["url"] for r in additions]
    updates={}
    expected=dict(live["colors"] or {})
    warnings=[]
    can_tag=bool(live["color_choices"] and live["colors"] is not None)
    all_rows=previous+additions
    for row in all_rows:
        n=row["slot"];p=photos[n]
        if n not in (1,*range(5,13)):
            continue
        if not p["metadata"] or not can_tag:
            warnings.append({"slot":n,"reason":"color_metadata_missing" if not p["metadata"] else "target_color_choices_unavailable"})
            continue
        try:
            color=template_name(pack["garment_code"],p["metadata"]["color_name"],live["color_choices"])
        except ValueError:
            warnings.append({"slot":n,"reason":"target_color_not_supported"})
            continue
        pos=str(row["position"])
        old=expected.get(pos)
        if old is not None:
            if old!=color:
                warnings.append({"slot":n,"reason":"existing_color_conflict_keep"})
            continue
        if color in expected.values():
            warnings.append({"slot":n,"reason":"color_already_assigned"})
            continue
        updates[row.get("url",pos)]=color
        expected[pos]=color
    if updates:
        changes["mockup_colors"]=updates
    total=len(all_rows)
    desired=min(9,total)
    if total and (additions or (live["gallery"] is not None and live["gallery"]!=desired)):
        changes["gallery"]=desired
    if total>=9 and [r["slot"] for r in all_rows[:9]]!=list(ORDER[:9]):
        warnings.append({"reason":"gallery_keeps_append_order_no_reorder"})
    unsent=[n for n in ORDER if n in photos and n not in {r["slot"] for r in all_rows}]
    if unsent and not allow_low_resolution and any(photos[n]["preview_only"] for n in unsent):
        warnings.append({"reason":"low_resolution_original_requires_confirmation"})
    if len(changes)==1:
        return {"policy":POLICY,"state":"nothing_to_send","warnings":warnings,
                "received_count":len(previous),"remaining_slots":unsent,
                "automatic_sms_writes":False},progress
    operation={"policy":POLICY,"idea_id":idea_id,"target_id":target_id,
               "pack_sha256":pack_sha(pack),"before":live,"additions":additions,
               "expected_count":total,"expected_gallery":changes.get("gallery",live["gallery"]),
               "expected_colors":expected if can_tag else None,
               "payload":{"idea_id":idea_id,"mode":"update","targets":[changes]}}
    operation["operation_id"]=digest(operation)
    return {**operation,"state":"review_required","warnings":warnings,
            "remaining_slots":unsent,"requires_explicit_confirmation":True,
            "automatic_sms_writes":False},progress

def reserve(progress,operation):
    if progress.get("pending") or operation.get("state")!="review_required":
        raise ValueError("cannot_reserve_sms_mockup_operation")
    return {**progress,"pending":{k:operation[k] for k in (
        "operation_id","pack_sha256","before","additions","expected_count",
        "expected_gallery","expected_colors","payload")}}

def record(progress,operation_id,live,*,outcome):
    """Operator-reported exact ordered readback, not a byte identity certificate."""
    if not isinstance(progress,dict) or not isinstance(progress.get("pending"),dict):
        raise ValueError("no_pending_sms_mockup_operation")
    op=progress["pending"]
    if operation_id!=op["operation_id"]:
        raise ValueError("sms_mockup_operation_changed")
    live=snapshot(live)
    before=op["before"]
    if outcome=="not_applied":
        if live!=before:
            raise ValueError("sms_snapshot_changed_reconcile_manually")
        return {**progress,"pending":None,"last_outcome":"operator_confirmed_not_applied"}
    if outcome not in ("applied","partially_applied"):
        raise ValueError("invalid_sms_mockup_outcome")
    count=len(live["files"]); old_count=len(before["files"])
    exact=count==op["expected_count"]
    partial=old_count<count<op["expected_count"]
    if ((outcome=="applied" and not exact) or (outcome=="partially_applied" and not partial)
        or live["files"][:old_count]!=before["files"]):
        raise ValueError("sms_mockup_readback_mismatch")
    received=list(progress["received"])
    for row,file in zip(op["additions"],live["files"][len(received):]):
        received.append({"slot":row["slot"],"position":row["position"],
            "output_sha256":row["output_sha256"],"sms_file":file["file"]})
    warnings=["partial_append_reconciled"] if outcome=="partially_applied" else []
    if op["expected_gallery"] is not None and live["gallery"]!=op["expected_gallery"]:
        warnings.append("gallery_not_confirmed")
    if op["expected_colors"] is not None:
        expected={k:v for k,v in op["expected_colors"].items() if int(k)<=count}
        if live["colors"]!=expected:
            warnings.append("colors_not_confirmed")
    return {**progress,"received":received,"pending":None,
            "last_outcome":"operator_reported_partial_prefix" if outcome=="partially_applied" else "operator_reported_counts_and_file_order",
            "last_operation_id":operation_id,"last_warnings":warnings,
            "last_gallery":live["gallery"],"last_colors":live["colors"],
            "sms_image_bytes_verified":False}
