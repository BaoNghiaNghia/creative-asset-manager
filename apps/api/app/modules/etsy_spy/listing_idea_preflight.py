"""Tenant-owned pre-SMS draft composition. No external writes or media copies."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.parse import urlsplit, parse_qs

SHA = re.compile(r"^[a-f0-9]{64}$")
DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{8,200}$")
GARMENTS = {"C1717", "G500", "G180", "G185"}


def drive_reference(url):
    if not isinstance(url, str) or len(url) > 2000 or url != url.strip():
        raise ValueError("invalid_drive_video_url")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "drive.google.com"
            or parsed.fragment):
        raise ValueError("invalid_drive_video_url")
    params = parse_qs(parsed.query, keep_blank_values=True)
    if set(params) - {"id", "usp", "resourcekey", "authuser", "export"}:
        raise ValueError("invalid_drive_video_url")
    file_match = re.fullmatch(r"/file/d/([A-Za-z0-9_-]{8,200})(?:/(?:view|preview|edit))?/?", parsed.path)
    folder = re.fullmatch(r"/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]{8,200})/?", parsed.path)
    if file_match or folder:
        if "id" in params:
            raise ValueError("invalid_drive_video_url")
        return (file_match or folder).group(1), "folder" if folder else "file"
    if parsed.path in ("/open", "/uc") and len(params.get("id", [])) == 1 and DRIVE_ID.fullmatch(params["id"][0]):
        return params["id"][0], "file"
    raise ValueError("invalid_drive_video_url")


def clean_video_links(entries):
    if not isinstance(entries, list) or len(entries) > 10:
        raise ValueError("invalid_drive_video_links")
    seen, aspects, output = set(), set(), []
    for item in entries:
        if not isinstance(item, dict) or set(item) - {"url", "aspect"}:
            raise ValueError("invalid_drive_video_link")
        url, aspect = item.get("url"), item.get("aspect")
        if aspect not in (None, "9:16", "16:9"):
            raise ValueError("invalid_video_aspect")
        ident, kind = drive_reference(url)
        url = canonical_view_url(url)
        if ident in seen or (aspect and aspect in aspects) or (kind == "folder" and aspect):
            raise ValueError("duplicate_or_invalid_video_link")
        seen.add(ident)
        if aspect:
            aspects.add(aspect)
        output.append({"url": url, "aspect": aspect, "kind": kind,
                       "source": "operator", "content_verified": False})
    return output


def table_video_links(job, projection, listing_key):
    """Link exactly the stored videos projected in Etsy Spy, irrespective of visual QA.

    The normal CAM upload already verifies Drive provenance. This is an internal
    reference, not a share grant, fresh Drive access probe or visual approval.
    Never export raw receipts, credentials, signed playback tickets or folders.
    """
    if (job is None or job.listing_key != listing_key
            or not SHA.fullmatch(job.artwork_sha256 or "")):
        return []
    receipt = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    uploads = receipt.get("drive_uploads")
    if not isinstance(uploads, dict):
        return []
    output, seen, aspects = [], set(), set()
    for video in projection.get("videos", []):
        ratio = video.get("aspectRatio")
        item = uploads.get(ratio)
        if ratio not in ("9:16", "16:9") or ratio in aspects or not isinstance(item, dict):
            continue
        ident, folder, digest = item.get("drive_file_id"), item.get("drive_folder_id"), item.get("sha256")
        if (video.get("storageVerified") is not True
                or item.get("storageVerified") is not True
                or item.get("storage_provider") != "google_drive_managed"
                or not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,128}", ident)
                or not isinstance(folder, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,128}", folder)
                or not isinstance(digest, str) or not SHA.fullmatch(digest)
                or video.get("sha256") != digest
                or ident in seen):
            continue
        seen.add(ident)
        aspects.add(ratio)
        output.append({"url": f"https://drive.google.com/file/d/{ident}/view",
                       "aspect": ratio, "kind": "file", "source": "cam_video_ugc_table",
                       "storage_verified": True, "content_verified": False,
                       "sha256": digest, "qa_required_for_sms": False})
    return output


def merge_video_links(table_links, manual_links):
    """Prefer current table assets; dedupe identical file IDs and aspect slots."""
    merged, seen, aspects = [], set(), set()
    for item in [*table_links, *manual_links]:
        ident, _ = drive_reference(item["url"])
        aspect = item.get("aspect")
        if ident in seen or (aspect and aspect in aspects):
            continue
        seen.add(ident)
        if aspect:
            aspects.add(aspect)
        merged.append(item)
    return merged


def canonical_view_url(url):
    """Normalize file references to view links; preserve the optional resource key."""
    ident, kind = drive_reference(url)
    keys = parse_qs(urlsplit(url).query, keep_blank_values=True).get("resourcekey", [])
    if keys and (len(keys) != 1 or not DRIVE_ID.fullmatch(keys[0])):
        raise ValueError("invalid_drive_resource_key")
    path = f"/file/d/{ident}/view" if kind == "file" else f"/drive/folders/{ident}"
    return "https://drive.google.com" + path + ("?resourcekey=" + keys[0] if keys else "")


def sms_video_fields(links):
    return [
        {"name": ("UGC Video Folder " + str(i + 1) if v["kind"] == "folder"
                  else "UGC Video " + str(v["aspect"] or i + 1) + " (Drive link)"),
         "value": canonical_view_url(v["url"])}
        for i, v in enumerate(links)
    ]


def sms_tiktok_target_video_plan(table_links):
    """Only the current storage-verified vertical video belongs in TikTok Videos.

    This is a candidate plan, not a prepared link or SMS write receipt.
    Manual links and folder links remain idea-level references only.
    """
    selected = []
    for item in table_links or []:
        if (not isinstance(item, dict) or item.get("aspect") != "9:16"
                or item.get("kind") != "file" or item.get("source") != "cam_video_ugc_table"
                or item.get("storage_verified") is not True
                or not SHA.fullmatch(item.get("sha256") or "")):
            continue
        try:
            ident, kind = drive_reference(item["url"])
            if kind != "file":
                continue
            url = canonical_view_url(item["url"])
        except (KeyError, TypeError, ValueError):
            continue
        selected.append({"aspect": "9:16", "file_id": ident, "sha256": item["sha256"],
                         "view_url": url})
        break
    return {"platform": "TiktokShop", "candidates": selected,
            "candidate_video_urls": [v["view_url"] for v in selected],
            "expected_target_video_count": len(selected),
            "prepare_required": bool(selected), "readback_required": bool(selected),
            "state": "needs_prepare" if selected else "no_verified_vertical_video",
            "fields_alone_satisfy_target": False}


def compose(*, listing_key, source, seo, designs, video_ugc, listing_image_count, saved, table_links=None):
    """Join existing read projections. Operator attestations never become live SMS receipts."""
    saved = saved if isinstance(saved, dict) else {}
    source = source or {}
    seo = seo or {}
    result = seo.get("result") if isinstance(seo.get("result"), dict) else {}
    assets = [a for a in designs if a.get("source") == "mapped" and SHA.fullmatch(a.get("output_sha256") or "")
              and a.get("position") not in (None, "unknown", "other")]
    chosen = next((a for a in assets if a.get("output_sha256") == saved.get("design_sha256")
                   and a.get("position") == saved.get("design_position")), None)
    if not saved.get("design_sha256") and len(assets) == 1:
        chosen = assets[0]
    blocks, warnings = [], ["listing_images_deferred", "draft_only_no_marketplace_publish"]
    def require(ok, name):
        if not ok:
            blocks.append(name)
    require(bool(source.get("title") and source.get("keyword")), "source_metadata_missing")
    require(chosen is not None, "select_current_design")
    require(saved.get("garment_code") in GARMENTS, "garment_missing")
    require(bool(result.get("title") and result.get("description")
                 and isinstance(result.get("highlights"), list) and len(result["highlights"]) >= 5
                 and isinstance(result.get("search_terms"), list) and len(result["search_terms"]) >= 6
                 and seo.get("status") == "needs_fact_check"), "seo_draft_missing_or_incomplete")
    require(bool(result and seo.get("job_id") and saved.get("seo_review_job_id") == seo.get("job_id")),
            "seo_fact_review_required")
    require(bool(saved.get("brief")), "creative_brief_missing")
    require(bool(saved.get("customer_insight")), "customer_insight_missing")
    require(bool(saved.get("occasion")), "occasion_missing")
    require(bool(saved.get("niche")), "niche_missing")
    require(bool(chosen and saved.get("rights_review_sha256") == chosen["output_sha256"]),
            "design_rights_review_required")
    template = saved.get("template") or {}
    require(bool(saved.get("shop") and template.get("id") and template.get("name")
                 and template.get("version") and template.get("shop") == saved.get("shop")
                 and template.get("garment_code") == saved.get("garment_code")),
            "sms_template_selection_required")
    template_sizes = template.get("sizes", [])
    priced_sizes = [p["size"] for p in template.get("prices", [])]
    package = template.get("package", {})
    require(bool(template_sizes and template.get("colors")
                 and template.get("size_chart") and template.get("currency")
                 and package.get("weight") and package.get("dimensions")
                 and len(priced_sizes) == len(set(priced_sizes))
                 and set(priced_sizes) == set(template_sizes)),
            "sms_template_details_missing")
    require(bool(saved.get("colors")) and set(saved.get("colors", [])) <= set(template.get("colors", [])),
            "template_color_selection_required")
    try:
        manual_links = clean_video_links(saved.get("video_links", []))
    except ValueError:
        manual_links = []
    links = merge_video_links(table_links or [], manual_links)
    target_plan = sms_tiktok_target_video_plan(table_links)
    if not links:
        warnings.append("google_drive_video_links_missing")
    if table_links and not target_plan["candidates"]:
        warnings.append("sms_tiktok_vertical_video_missing")
    if manual_links:
        warnings.append("operator_drive_links_access_and_content_unverified")
    if table_links:
        warnings.append("table_video_links_no_visual_qa_gate_drive_access_not_rechecked")
    if not saved.get("skus"):
        warnings.append("seller_sku_and_sellable_stock_deferred_until_marketplace_listing")
    if template:
        warnings.append("operator_imported_template_revalidate_with_sms_before_send")
    if not listing_image_count:
        warnings.append("no_listing_images_yet")
    identity = None
    if chosen and saved.get("garment_code") in GARMENTS:
        identity = ":".join((listing_key, chosen["output_sha256"], saved["garment_code"], chosen["position"]))
    return {
        "schema": "cam-listing-idea-preflight-v1", "listingKey": listing_key,
        "identity": identity, "mode": "metadata_video_only",
        "source": source, "seo": {"status": seo.get("status"), "job_id": seo.get("job_id"),
          "title": result.get("title"), "description": result.get("description"),
          "highlights": result.get("highlights", []), "search_terms": result.get("search_terms", [])},
        "designs": assets, "selected_design": chosen,
        "video_ugc": video_ugc, "video_links": links,
        "sms_video_fields": sms_video_fields(links),
        "sms_video_policy": "table_visible_no_visual_qa_gate",
        "sms_video_transport": "drive_links_only",
        "sms_video_payload_policy": "target_video_urls_required_when_available",
        "sms_target_video_plan": target_plan,
        "sms_target_video_readback_required": target_plan["readback_required"],
        "sms_server_video_fetch_possible": True,
        "sms_video_share_prepare_required": True,
        "sms_video_upload_requested": False,
        "listing_image_count": listing_image_count, "saved": saved,
        "blockers": blocks, "warnings": warnings,
        "ready_for_sms_payload_review": not blocks, "ready_for_sms_submit": False,
        "submitted_to_sms": False,
        "requires_live_sms_template_and_duplicate_check": True,
        "updated_at": saved.get("updated_at"),
    }


def validate_draft(raw):
    """Bounded operator input; no implicit claims about SMS stock or upload."""
    if not isinstance(raw, dict):
        raise ValueError("invalid_listing_idea_draft")
    allowed = {"design_sha256", "design_position", "garment_code", "shop", "template",
               "brief", "customer_insight", "occasion", "niche", "colors", "video_links",
               "rights_review_sha256", "seo_review_job_id", "skus"}
    if set(raw) - allowed:
        raise ValueError("unknown_listing_idea_field")
    clean = {}
    for key, maximum in (("design_position", 40), ("garment_code", 10), ("shop", 120),
                         ("brief", 4000), ("customer_insight", 4000), ("occasion", 180),
                         ("niche", 180), ("seo_review_job_id", 80)):
        val = raw.get(key)
        if val is not None:
            if not isinstance(val, str) or len(val) > maximum:
                raise ValueError("invalid_" + key)
            clean[key] = val.strip()
    for key in ("design_sha256", "rights_review_sha256"):
        if raw.get(key) is not None:
            if not isinstance(raw[key], str) or not SHA.fullmatch(raw[key]):
                raise ValueError("invalid_" + key)
            clean[key] = raw[key]
    if clean.get("garment_code") and clean["garment_code"] not in GARMENTS:
        raise ValueError("invalid_garment")
    colors = raw.get("colors", [])
    if not isinstance(colors, list) or len(colors) > 72 or any(not isinstance(c, str) or not c.strip() or len(c) > 80 for c in colors):
        raise ValueError("invalid_colors")
    clean["colors"] = list(dict.fromkeys(colors))
    clean["video_links"] = [{"url": v["url"], "aspect": v["aspect"]} for v in clean_video_links(raw.get("video_links", []))]
    template = raw.get("template")
    if template is not None:
        if not isinstance(template, dict) or set(template) - {"id", "name", "version", "shop", "garment_code",
                "prices", "sizes", "colors", "size_chart", "package", "currency", "attributes"}:
            raise ValueError("invalid_template")
        for key in ("id", "name", "shop", "garment_code", "size_chart", "currency"):
            if key in template and (not isinstance(template[key], str) or len(template[key]) > 250):
                raise ValueError("invalid_template_" + key)
        if type(template.get("version")) is not int or template["version"] < 1:
            raise ValueError("invalid_template_version")
        for key, cap in (("sizes", 20), ("colors", 72)):
            vals = template.get(key, [])
            if not isinstance(vals, list) or len(vals) > cap or any(not isinstance(v, str) or not v or len(v) > 80 for v in vals):
                raise ValueError("invalid_template_" + key)
        prices = template.get("prices", [])
        if (not isinstance(prices, list) or len(prices) > 100 or any(
                not isinstance(p, dict) or set(p) != {"size", "price"} or
                not isinstance(p["size"], str) or p["size"] not in template.get("sizes", []) or
                type(p["price"]) not in (int, float) or not 0 < p["price"] < 100000 for p in prices)):
            raise ValueError("invalid_template_prices")
        package = template.get("package", {})
        if not isinstance(package, dict) or set(package) - {"weight", "dimensions"} or any(
                not isinstance(v, str) or len(v) > 100 for v in package.values()):
            raise ValueError("invalid_template_package")
        if "attributes" in template:
            attrs = template["attributes"]
            if not isinstance(attrs, list) or len(attrs) > 100 or any(
                    not isinstance(a, dict) or set(a) != {"name", "values"}
                    or not isinstance(a["name"], str) or len(a["name"]) > 250
                    or not isinstance(a["values"], list) or len(a["values"]) > 30
                    or any(not isinstance(v, str) or len(v) > 250 for v in a["values"])
                    for a in attrs):
                raise ValueError("invalid_template_attributes")
        clean["template"] = template
    if "skus" in raw:
        skus = raw["skus"]
        if not isinstance(skus, list) or len(skus) > 500 or any(
                not isinstance(x, dict) or set(x) - {"sku", "color", "size"} or
                any(not isinstance(x.get(key), str) or not x[key].strip() or len(x[key]) > 120
                    for key in ("sku", "color", "size")) for x in skus):
            raise ValueError("invalid_skus")
        clean["skus"] = skus
    clean["updated_at"] = datetime.now(timezone.utc).isoformat()
    return clean
