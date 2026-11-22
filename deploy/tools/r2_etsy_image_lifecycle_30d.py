#!/usr/bin/env python3
"""Guarded 30-day R2 lifecycle for private, disposable Etsy image derivatives.

Uses the existing R2 S3 credentials, not a new Cloudflare API token. The
bucket-wide PUT is only allowed after reading and validating every existing
rule. It changes only etsy-images/v1/, and preserves all other rules.
Never prints credentials, object names, tenant IDs, or remote error bodies.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from botocore.exceptions import BotoCoreError, ClientError
from botocore.config import Config

try:
    from .production_env import parse_environment_file
except ImportError:
    from production_env import parse_environment_file

BUCKET = "creative-asset-video-cache"
PREFIX = "etsy-images/v1/"
RULE_ID = "cam-etsy-images-v1-expire-30d"
DAYS = 30
RULE = {
    "ID": RULE_ID,
    "Status": "Enabled",
    "Filter": {"Prefix": PREFIX},
    "Expiration": {"Days": DAYS},
}


class RetentionBlocked(Exception):
    pass


def validate_existing(rules: list) -> tuple[list, bool]:
    """Refuse conflicting expiration; preserve video and multipart rules."""
    if not isinstance(rules, list) or len(rules) >= 1000:
        raise RetentionBlocked("unsafe_lifecycle_list")
    ids: set[str] = set()
    configured = False
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("ID"), str):
            raise RetentionBlocked("invalid_existing_rule")
        rid = rule["ID"]
        if rid in ids:
            raise RetentionBlocked("duplicate_existing_rule")
        ids.add(rid)
        prefix = rule.get("Filter", {}).get("Prefix") if isinstance(rule.get("Filter"), dict) else rule.get("Prefix")
        if rid == RULE_ID:
            if rule.get("Status") != "Enabled" or prefix != PREFIX or rule.get("Expiration") != {"Days": DAYS}:
                raise RetentionBlocked("existing_image_rule_conflict")
            configured = True
            continue
        if "Expiration" in rule and rule.get("Status") == "Enabled":
            # Filters with tags or more complicated matching are too ambiguous
            # to declare safe; fail closed rather than silently change retention.
            if not isinstance(prefix, str) or PREFIX.startswith(prefix) or prefix.startswith(PREFIX):
                raise RetentionBlocked("overlapping_existing_expiration")
    return ([*rules] if configured else [*rules, dict(RULE)], configured)


def make_client(values: dict[str, str]):
    import boto3
    if values.get("R2_BUCKET_NAME") != BUCKET:
        raise RetentionBlocked("unexpected_bucket")
    endpoint = values.get("R2_ENDPOINT", "")
    parsed = urlsplit(endpoint)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password or
            parsed.port or parsed.path not in ("", "/") or parsed.query or parsed.fragment or
            not host.endswith(".r2.cloudflarestorage.com") or
            not re.fullmatch("[0-9a-f]{32}", host.split(".")[0])):
        raise RetentionBlocked("invalid_r2_endpoint")
    if not values.get("R2_ACCESS_KEY_ID") or not values.get("R2_SECRET_ACCESS_KEY"):
        raise RetentionBlocked("r2_credentials_unavailable")
    return boto3.client(
        "s3", endpoint_url=endpoint.rstrip("/"), region_name="auto",
        aws_access_key_id=values["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=values["R2_SECRET_ACCESS_KEY"],
        config=Config(s3={"addressing_style": "path"},
                      retries={"max_attempts": 1},
                      connect_timeout=2, read_timeout=6),
    )


def fetch_rules(client) -> list:
    try:
        result = client.get_bucket_lifecycle_configuration(Bucket=BUCKET)
        rules = result.get("Rules")
        if not isinstance(rules, list):
            raise RetentionBlocked("lifecycle_rules_unavailable")
        return rules
    except ClientError as exc:
        error = str(exc.response.get("Error", {}).get("Code") or "")
        if error == "NoSuchLifecycleConfiguration":
            return []
        raise RetentionBlocked("lifecycle_read_rejected") from None
    except (BotoCoreError, OSError):
        raise RetentionBlocked("lifecycle_read_unavailable") from None


def operate(action: str, values: dict[str, str] | None = None, *, client=None) -> dict[str, object]:
    if action == "plan":
        return {"operation": "plan", "bucket": BUCKET, "prefix": PREFIX,
                "days": DAYS, "existing_rules": "preserved",
                "cloudflare_api_token_required": False}
    if action not in {"check", "apply"}:
        raise RetentionBlocked("invalid_operation")
    remote = client if client is not None else make_client(values or {})
    before = fetch_rules(remote)
    desired, present = validate_existing(before)
    if action == "check" or present:
        return {"operation": action, "configured": present, "changed": False,
                "prefix": PREFIX, "days": DAYS, "preserved_rule_count": len(before) - int(present)}
    try:
        remote.put_bucket_lifecycle_configuration(
            Bucket=BUCKET, LifecycleConfiguration={"Rules": desired})
    except (ClientError, BotoCoreError, OSError):
        raise RetentionBlocked("lifecycle_write_rejected") from None
    after = fetch_rules(remote)
    _, verified = validate_existing(after)
    before_by_id = {r["ID"]: r for r in before}
    after_by_id = {r["ID"]: r for r in after}
    if not verified or any(after_by_id.get(rid) != rule for rid, rule in before_by_id.items()):
        raise RetentionBlocked("postwrite_verification_failed")
    return {"operation": "apply", "configured": True, "changed": True,
            "prefix": PREFIX, "days": DAYS, "preserved_rule_count": len(before)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "check", "apply"))
    parser.add_argument("--env-file", type=Path, default=Path("/etc/creative-asset-manager/production.env"))
    parser.add_argument("--confirm-prefix", default="")
    args = parser.parse_args()
    try:
        if args.action == "apply" and args.confirm_prefix != PREFIX:
            raise RetentionBlocked("confirm_exact_prefix_required")
        values = None if args.action == "plan" else parse_environment_file(args.env_file)
        result = operate(args.action, values)
        print(json.dumps(result, sort_keys=True))
        return 0 if args.action == "plan" or result["configured"] else 2
    except (RetentionBlocked, OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, RetentionBlocked) else "configuration_unavailable"
        print(json.dumps({"operation": args.action, "status": "blocked", "reason": reason}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
