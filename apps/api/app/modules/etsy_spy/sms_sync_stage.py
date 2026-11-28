"""Operator-staged SMS evidence, never a live SMS fetch or writer."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from .listing_sms_sync import SHOP, LISTING, UUID, SMS_CODE, SHA, RECEIPT_KEY, valid, same_view_file
from .listing_idea_preflight import drive_reference

ROOT = Path("/opt/creative-asset-manager/sms-sync-staged")
SCHEMA = "cam-sms-sync-v3-operator-stage-v1"
BUNDLE = "cam-sms-sync-v3-operator-receipts-v1"
MAX_BYTES = 128 * 1024
KEYS = {"confirm", "candidate_id", "idea_code", "idea_id", "target_id", "shop",
        "reference_images", "target_videos", "listings_made", "listings_live",
        "etsy_id", "vertical_field_url"}


def stage_path(tenant):
    return ROOT / (hashlib.sha256(tenant.encode("utf-8")).hexdigest() + ".json")


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def validate_stage(data, tenant):
    if (not isinstance(data, dict) or data.get("schema") != SCHEMA
            or data.get("tenant_id") != tenant or data.get("shop") != SHOP
            or not _timestamp(data.get("observed_at"))):
        raise ValueError("invalid_sms_stage")
    rows = data.get("receipts")
    held = data.get("held")
    quota = data.get("quota")
    if (not isinstance(rows, list) or not 1 <= len(rows) <= 50
            or not isinstance(held, list) or len(held) > 50
            or not isinstance(quota, dict) or quota.get("state") not in {"SMS_DAILY_LIMIT", "UNKNOWN", "REVIEW_AFTER_LIMIT"}):
        raise ValueError("invalid_sms_stage")
    seen = {key: set() for key in ("etsy_id", "idea_code", "idea_id", "target_id")}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != KEYS or row["confirm"] is not True
                or row["shop"] != SHOP or not valid(LISTING, row["etsy_id"])
                or not valid(SHA, row["candidate_id"]) or not valid(SMS_CODE, row["idea_code"])
                or not valid(UUID, row["idea_id"]) or not valid(UUID, row["target_id"])
                or any(type(row[k]) is not int for k in ("reference_images", "target_videos", "listings_made", "listings_live"))
                or (row["reference_images"], row["target_videos"], row["listings_made"], row["listings_live"]) != (2,1,0,0)):
            raise ValueError("invalid_sms_stage_receipt")
        try:
            file_id, kind = drive_reference(row["vertical_field_url"])
        except (ValueError, TypeError):
            raise ValueError("invalid_sms_stage_link") from None
        if kind != "file" or not same_view_file(row["vertical_field_url"], file_id):
            raise ValueError("invalid_sms_stage_link")
        for key, values in seen.items():
            value = row[key].casefold()
            if value in values:
                raise ValueError("duplicate_sms_stage_identity")
            values.add(value)
    held_ids = set()
    for item in held:
        if (not isinstance(item, dict) or set(item) != {"etsy_id", "state"}
                or not valid(LISTING, item["etsy_id"]) or item["etsy_id"] in seen["etsy_id"]
                or item["etsy_id"] in held_ids or item["state"] not in {"SMS_DAILY_LIMIT","REVIEW_REQUIRED"}):
            raise ValueError("invalid_sms_stage_held")
        held_ids.add(item["etsy_id"])
    if quota["state"] == "SMS_DAILY_LIMIT" and (
            quota.get("limit") != 20 or not _timestamp(quota.get("observed_at"))
            or quota.get("reset_at") is not None):
        raise ValueError("invalid_sms_stage_quota")
    return data


def load_stage(tenant):
    try:
        with stage_path(tenant).open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > MAX_BYTES:
        raise ValueError("sms_stage_too_large")
    return validate_stage(json.loads(raw), tenant)


def project_stage(data, saved):
    if data is None:
        return {"schema": BUNDLE, "shop": SHOP, "state": "not_prepared", "receipts": [],
                "live_sms_verified_by_cam": False}
    pending, recorded, review = [], [], []
    for row in data["receipts"]:
        key = row["etsy_id"]
        if key not in saved:
            review.append({"etsy_id": key, "idea_code": row["idea_code"], "reason": "not_picked"})
            continue
        draft = saved[key]
        old = draft.get(RECEIPT_KEY) if isinstance(draft, dict) else None
        if old is None:
            pending.append(row)
        elif isinstance(old, dict) and all(old.get(k) == v for k,v in row.items() if k != "confirm"):
            recorded.append({"etsy_id": key, "idea_code": row["idea_code"]})
        else:
            review.append({"etsy_id": key, "idea_code": row["idea_code"], "reason": "existing_receipt_differs"})
    return {"schema": BUNDLE, "shop": SHOP, "state": "prepared",
            "observed_at": data["observed_at"], "sms_observed_count": len(data["receipts"]),
            "recorded_count": len(recorded), "pending_count": len(pending), "review_count": len(review),
            "held_count": len(data["held"]), "quota": data["quota"], "receipts": pending,
            "review": review, "live_sms_verified_by_cam": False}
