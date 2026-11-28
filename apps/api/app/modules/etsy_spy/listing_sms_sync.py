"""CAM_SMS_SYNC_V2: metadata-only, SHA-bound Idea handoff and media reconciliation.

The CAM database is evidence for saved media, not a live SMS receipt. Never
delete/re-render assets or publish listings from this read-side planner.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re
from urllib.parse import urlsplit

from .listing_idea_preflight import (SHA, sms_video_fields, sms_tiktok_target_video_plan,
                                     drive_reference, canonical_view_url)
from .design_artifacts import design_media_path

POLICY = "CAM_SMS_SYNC_V2"
SHOP = "Tiktok_McGowanshopdesigns"
GARMENT = "C1717"
ROOT = "1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl"
RECEIPT_KEY = "_sms_sync_v2_readback"
DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{10,128}")
ASSET_ID = re.compile(r"[0-9a-f]{24}")
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
LISTING = re.compile(r"[0-9]{5,20}")
SMS_CODE = re.compile(r"I[0-9]{1,12}")


def valid(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def current_designs(extraction, designs):
    """Only mapped receipts from the latest, non-cancelled extraction."""
    if extraction is None or extraction.cancellation_requested:
        return []
    payload = extraction.payload_json
    rows = payload.get("design_assets") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    result = []
    for item in designs:
        if (item.get("source") != "mapped" or item.get("job_id") != extraction.id
                or item.get("listing_key") != extraction.entity_id
                or not valid(ASSET_ID, item.get("id"))
                or not valid(SHA, item.get("output_sha256"))
                or item.get("original_url") != design_media_path(extraction.id, item["id"])):
            continue
        matches = [r for r in rows if isinstance(r, dict) and r.get("id") == item["id"]
                   and r.get("output_sha256") == item["output_sha256"]]
        if len(matches) != 1:
            continue
        raw = matches[0]
        if (raw.get("storage_provider") != "google_drive_managed"
                or raw.get("storage_folder_id") != ROOT
                or not valid(DRIVE_ID, raw.get("drive_file_id"))
                or not valid(DRIVE_ID, raw.get("drive_thumbnail_id"))
                or raw["drive_file_id"] == raw["drive_thumbnail_id"]):
            continue
        result.append((item, raw))
    return result


def muse_master(extraction, designs, muse):
    matches = current_designs(extraction, designs)
    if muse is None:
        return None, matches
    selected = [(item, raw) for item, raw in matches
                if item["original_url"] == muse.artwork_asset_path
                and item["output_sha256"] == muse.artwork_sha256
                and item["listing_key"] == muse.listing_key
                and raw.get("artwork_kind") == "native_master_v2"]
    return selected[0] if len(selected) == 1 else None, matches


def audit_media(extraction, designs, muse, rows):
    """Never infer stale/deleted from an unavailable latest master.

    A different valid placement/variant is not automatically stale. V13
    missing slots are advisory for legacy packs without a persisted role count.
    """
    master, known = muse_master(extraction, designs, muse)
    current_shas = {item["output_sha256"] for item, _ in known}
    video_sha = getattr(muse, "artwork_sha256", None)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.pack_id].append(row)
    packs = []
    for pack_id, group in sorted(grouped.items()):
        identity = {(r.garment_code, r.design_sha256, r.source_sha256, r.placement) for r in group}
        slots = sorted({r.slot for r in group if type(r.slot) is int and 1 <= r.slot <= 13})
        mixed = len(identity) != 1 or len(slots) != len(group)
        garment, digest, source, placement = next(iter(identity)) if len(identity) == 1 else (None, None, None, None)
        if mixed:
            state = "ambiguous_pack"
        elif not current_shas:
            state = "master_unresolved"
        elif digest not in current_shas:
            state = "not_in_current_managed_designs"
        elif video_sha and digest != video_sha:
            state = "other_current_design"
        elif master and digest == master[0]["output_sha256"]:
            state = "matches_video_master"
        else:
            state = "current_design_no_video_match"
        low_resolution = [r.slot for r in group if not isinstance(r.native_width, int)
                          or not isinstance(r.native_height, int)
                          or min(r.native_width, r.native_height) < 2000]
        # Role count was not persisted in historical receipts. Slots 11-13
        # prove V13; a manifest proves a complete legacy/V13 pack as stored.
        manifests = {r.drive_manifest_id for r in group if r.drive_manifest_id}
        manifest_ready = (not mixed and len(manifests) == 1 and not low_resolution
                          and all(r.drive_manifest_id in manifests for r in group)
                          and slots in (list(range(1, 11)), list(range(1, 14))))
        known_count = 13 if any(x > 10 for x in slots) else (10 if manifest_ready else None)
        missing = [x for x in range(1, 14) if x not in slots]
        packs.append({"pack_id": pack_id, "garment_code": garment,
                      "design_sha256": digest, "source_sha256": source, "placement": placement,
                      "state": state, "saved_slots": slots, "missing_main": 1 not in slots,
                      "expected_slots_known": known_count,
                      "missing_v13_slots_advisory": missing,
                      "missing_required_slots": ([x for x in range(1, known_count + 1) if x not in slots]
                                                 if known_count else []),
                      "preview_only_slots": sorted(low_resolution), "manifest_ready": manifest_ready})
    matching = [p for p in packs if p["state"] == "matches_video_master"]
    return {"policy": POLICY, "current_video_master_verified": master is not None,
            "video_artwork_sha256": video_sha, "current_managed_design_count": len(known),
            "image_count": len(rows), "pack_count": len(packs),
            "missing_main": not any(1 in p["saved_slots"] for p in packs),
            "matching_video_master_pack_count": len(matching),
            "matching_video_master_has_main": any(not p["missing_main"] for p in matching),
            "different_from_video_design_count": sum(1 for r in rows if video_sha and r.design_sha256 != video_sha),
            "noncurrent_design_pack_count": sum(p["state"] == "not_in_current_managed_designs" for p in packs),
            "other_current_design_pack_count": sum(p["state"] == "other_current_design" for p in packs),
            "ambiguous_pack_count": sum(p["state"] == "ambiguous_pack" for p in packs),
            "unresolved_pack_count": sum(p["state"] == "master_unresolved" for p in packs),
            "preview_only_count": sum(len(p["preview_only_slots"]) for p in packs),
            "manifest_ready_count": sum(p["manifest_ready"] for p in packs),
            "packs": packs, "repair_is_advisory": True}


def safe_product_image(value):
    if not isinstance(value, str) or len(value) > 1024:
        return False
    try:
        p = urlsplit(value)
        return (p.scheme == "https" and p.netloc == "i.etsystatic.com" and p.path
                and not p.fragment)
    except ValueError:
        return False


def bounded_text(value, max_length):
    return isinstance(value, str) and 0 < len(value.strip()) <= max_length and len(value) <= max_length


def digest_json(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def idea_plan(*, listing_key, source, seo, extraction, designs, muse, table_links, saved):
    """Produce a complete review candidate; preparation and live SMS check remain separate."""
    source = source or {}
    seo = seo or {}
    saved = saved if isinstance(saved, dict) else {}
    result = seo.get("result") if isinstance(seo.get("result"), dict) else {}
    master, _ = muse_master(extraction, designs, muse)
    target = sms_tiktok_target_video_plan(table_links)
    blocks = []
    def require(ok, code):
        if not ok:
            blocks.append(code)
    require(valid(LISTING, listing_key) and bounded_text(source.get("title"), 700)
            and bounded_text(source.get("keyword"), 220), "source_metadata_missing")
    require(master is not None and getattr(muse, "listing_key", None) == listing_key,
            "video_master_not_current_managed_design")
    require(saved.get("shop") in (None, "", SHOP), "unexpected_shop_for_default_handoff")
    require(saved.get("garment_code") in (None, "", GARMENT), "garment_requires_operator_selection")
    if saved.get("design_sha256"):
        require(master is not None and saved["design_sha256"] == master[0]["output_sha256"],
                "selected_design_differs_from_video")
    photo = getattr(muse, "product_image_url", None)
    require(safe_product_image(photo), "product_reference_missing")
    require(target["expected_target_video_count"] == 1, "verified_vertical_video_missing")
    title, description = result.get("title"), result.get("description")
    highlights, terms = result.get("highlights"), result.get("search_terms")
    # tiktok_skill explicitly labels this seller-requested placeholder
    # unverified. It belongs in the internal SEO draft, not the SMS handoff.
    if isinstance(highlights, list):
        highlights = [line for line in highlights if line !=
                      "Printed and shipped in the USA – no import customs fees"]
    require(seo.get("status") == "needs_fact_check" and bounded_text(title, 500)
            and bounded_text(description, 10000)
            and isinstance(highlights, list) and 4 <= len(highlights) <= 10
            and all(bounded_text(x, 500) for x in highlights[:5])
            and isinstance(terms, list) and 6 <= len(terms) <= 50
            and all(bounded_text(x, 250) for x in terms)
            and sum(len(x) + 1 for x in terms) <= 2048, "seo_draft_missing_or_incomplete")
    out = {"policy": POLICY, "mode": "idea_only", "shop": SHOP, "garment": GARMENT,
           "dedupe_key": "etsy:" + listing_key + ":" + SHOP.casefold(),
           "blockers": blocks, "ready_for_idea_review": not blocks,
           "requires_live_duplicate_check": True, "requires_explicit_confirmation": True,
           "requires_prepared_video_url": True, "requires_reference_link_check": True,
           "requires_target_readback": True,
           "fields_alone_satisfy_target": False, "marketplace_publish_allowed": False,
           "sms_server_video_fetch_possible": True, "sms_upload_requested": False,
           "payload": None, "candidate_id": None,
           "readback_state": "not_reported"}
    receipt = saved.get(RECEIPT_KEY)
    if isinstance(receipt, dict):
        out["readback_state"] = "stale_report"
        out["last_reported_readback"] = {k: receipt.get(k) for k in (
            "candidate_id", "idea_code", "idea_id", "target_id", "reference_images",
            "target_videos", "reported_at", "evidence")}
    if blocks:
        return out
    item, raw = master
    candidate = target["candidates"][0]
    # The explicitly shared root allows this narrow thumbnail reference.
    design_preview = "https://drive.google.com/thumbnail?id=" + raw["drive_thumbnail_id"] + "&sz=w720"
    video_fields = sms_video_fields(table_links)
    payload = {
        "title": title, "brief": saved["brief"] if bounded_text(saved.get("brief"), 4000) else source["title"],
        "notes": "Draft only. Verify SEO and design rights before publishing. Video QA is not required for Idea handoff.",
        "priority": "normal", "keywords": terms,
        "fields": [{"name": "Etsy ID", "value": listing_key},
                   {"name": "Etsy Source URL", "value": "https://www.etsy.com/listing/" + listing_key},
                   {"name": "Research keyword", "value": source["keyword"]},
                   {"name": "Garment đề xuất", "value": GARMENT},
                   {"name": "Description", "value": description},
                   {"name": "Highlights", "value": "\n".join(highlights[:5])}] + video_fields,
        "image_urls": [photo, design_preview],
        "targets": [{"shop": SHOP, "variant_label": GARMENT + " - " + title[:111],
                     "title": title, "description": description, "keywords": terms,
                     "highlights": highlights[:5], "video_urls": [candidate["view_url"]]}],
    }
    candidate_id = digest_json({"policy": POLICY, "listing_key": listing_key, "master": item["output_sha256"],
                                "video_sha": candidate["sha256"], "payload": payload})
    out.update({"payload": payload, "candidate_id": candidate_id,
                "design_sha256": item["output_sha256"], "design_asset_id": item["id"],
                "video": candidate, "expected_target_video_count": 1,
                "payload_is_review_candidate_only": True})
    if isinstance(receipt, dict) and receipt.get("candidate_id") == candidate_id:
        out["readback_state"] = "reported_counts_match"
    return out


def same_view_file(url, file_id):
    try:
        ident, kind = drive_reference(url)
        return kind == "file" and ident == file_id and url == canonical_view_url(url)
    except (TypeError, ValueError):
        return False


def validate_readback(raw, plan):
    """Accept an attributable external MCP count report, not a live SMS proof."""
    keys = {"confirm", "candidate_id", "idea_code", "idea_id", "target_id", "shop",
            "reference_images", "target_videos", "listings_made", "listings_live",
            "etsy_id", "vertical_field_url"}
    if (not isinstance(raw, dict) or set(raw) != keys or raw["confirm"] is not True
            or not plan.get("ready_for_idea_review")
            or not valid(SHA, raw["candidate_id"]) or raw["candidate_id"] != plan.get("candidate_id")
            or not valid(SMS_CODE, raw["idea_code"]) or not valid(UUID, raw["idea_id"])
            or not valid(UUID, raw["target_id"]) or raw["shop"] != plan["shop"]
            or raw["etsy_id"] != plan["dedupe_key"].split(":")[1]
            or not same_view_file(raw["vertical_field_url"], plan["video"]["file_id"])
            or any(type(raw[k]) is not int for k in ("reference_images", "target_videos", "listings_made", "listings_live"))
            or raw["reference_images"] != 2 or raw["target_videos"] != 1
            or raw["listings_made"] != 0 or raw["listings_live"] != 0):
        raise ValueError("invalid_or_stale_sms_readback")
    return {k: raw[k] for k in keys - {"confirm"}} | {
        "evidence": "operator_reported_mcp_counts_and_fields",
        "target_video_identity_verified": False, "live_sms_verified_by_cam": False}
