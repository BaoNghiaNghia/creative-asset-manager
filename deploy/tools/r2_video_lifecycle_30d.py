#!/usr/bin/env python3
"""Guarded Cloudflare R2 30-day lifecycle for *only* video-cache/ keys.

Cloudflare lifecycle PUT replaces ALL bucket rules. This tool GETs the current
rules and merges just our one rule; it refuses unknown conflicting delete
rules, missing credentials, or an unexpected bucket. It never touches Etsy
images or private artwork. No token, object keys or tenant IDs are printed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if __package__:
    from .production_env import parse_environment_file
else:
    from production_env import parse_environment_file

BUCKET = "creative-asset-video-cache"
PREFIX = "video-cache/"
DAYS = 30
ID = "cam-video-cache-expire-30d"
RULE = {
    "id": ID,
    "enabled": True,
    "conditions": {"prefix": PREFIX},
    "deleteObjectsTransition": {
        "condition": {"type": "Age", "maxAge": DAYS * 86400},
    },
}


class LifecycleBlocked(Exception):
    pass


def _api(account_id: str, token: str, method: str, payload=None) -> dict:
    if not re.fullmatch(r"[0-9a-fA-F]{32}", account_id or ""):
        raise LifecycleBlocked("cloudflare_account_unavailable")
    if not token or len(token) < 30:
        raise LifecycleBlocked("cloudflare_api_token_unavailable")
    url = (f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
           f"/r2/buckets/{BUCKET}/lifecycle")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, method=method, data=body, headers=headers), timeout=12) as response:
            obj = json.load(response)
    except HTTPError as exc:
        raise LifecycleBlocked("cloudflare_api_http_" + str(exc.code)) from None
    except (URLError, OSError, ValueError):
        raise LifecycleBlocked("cloudflare_api_unavailable") from None
    if not isinstance(obj, dict) or obj.get("success") is not True:
        raise LifecycleBlocked("cloudflare_api_rejected")
    return obj.get("result") or {}


def _same_rule(rule: dict) -> bool:
    return (
        rule.get("id") == ID and rule.get("enabled") is True and
        rule.get("conditions", {}).get("prefix") == PREFIX and
        rule.get("deleteObjectsTransition", {}).get("condition") ==
        RULE["deleteObjectsTransition"]["condition"]
    )


def merge_rules(current: list) -> tuple[list, bool]:
    if not isinstance(current, list) or len(current) > 999:
        raise LifecycleBlocked("invalid_rule_list")
    ids = set()
    found = False
    for rule in current:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
            raise LifecycleBlocked("invalid_existing_rule")
        rid = rule["id"]
        if rid in ids:
            raise LifecycleBlocked("duplicate_existing_rule")
        ids.add(rid)
        if rid == ID:
            if not _same_rule(rule):
                raise LifecycleBlocked("existing_retention_rule_conflict")
            found = True
            continue
        prefix = rule.get("conditions", {}).get("prefix")
        if not isinstance(prefix, str):
            raise LifecycleBlocked("existing_rule_prefix_unknown")
        # A pre-existing delete matching any part of video-cache/ can
        # silently alter the 30-day policy; don't overwrite/remove it.
        if rule.get("enabled") and rule.get("deleteObjectsTransition") and (
            PREFIX.startswith(prefix) or prefix.startswith(PREFIX)
        ):
            raise LifecycleBlocked("overlapping_existing_delete_rule")
    return (list(current) if found else [*current, RULE], found)


def run(action: str, env_file: Path) -> dict:
    if action == "plan":
        return {"operation": "plan", "bucket": BUCKET, "rule": RULE,
                "other_bucket_rules": "preserved"}
    values = parse_environment_file(env_file)
    if values.get("R2_BUCKET_NAME") != BUCKET:
        raise LifecycleBlocked("wrong_bucket")
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or values.get("R2_ACCOUNT_ID", "")
    existing = _api(account, token, "GET")
    rules = existing.get("rules")
    if not isinstance(rules, list):
        raise LifecycleBlocked("current_rules_unavailable")
    desired, configured = merge_rules(rules)
    if action == "check":
        return {"operation": action, "configured": configured,
                "bucket": BUCKET, "prefix": PREFIX, "days": DAYS,
                "preserved_rule_count": len(rules) - int(configured)}
    if configured:
        return {"operation": action, "configured": True, "changed": False,
                "bucket": BUCKET, "prefix": PREFIX, "days": DAYS}
    _api(account, token, "PUT", {"rules": desired})
    verified = _api(account, token, "GET").get("rules")
    if not isinstance(verified, list) or not any(_same_rule(rule) for rule in verified):
        raise LifecycleBlocked("lifecycle_postwrite_verification_failed")
    original_ids = {r["id"] for r in rules}
    if not original_ids.issubset({r.get("id") for r in verified}):
        raise LifecycleBlocked("other_lifecycle_rules_changed_after_write")
    return {"operation": action, "configured": True, "changed": True,
            "bucket": BUCKET, "prefix": PREFIX, "days": DAYS,
            "preserved_rule_count": len(rules)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "check", "apply"))
    parser.add_argument("--env-file", type=Path,
                        default=Path("/etc/creative-asset-manager/production.env"))
    args = parser.parse_args()
    try:
        result = run(args.action, args.env_file)
        print(json.dumps(result, sort_keys=True))
        return 0 if args.action != "check" or result["configured"] else 2
    except (LifecycleBlocked, FileNotFoundError, OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, LifecycleBlocked) else "config_unavailable"
        print(json.dumps({"operation": args.action, "status": "blocked", "reason": reason},
                         sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
