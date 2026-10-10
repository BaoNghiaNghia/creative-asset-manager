"""Remove only Stage 1 owned Google Drive previews older than 48 hours.

Dry-run default. Scopes to explicitly named temp folder and appProperties
written by CAM Stage 1 uploader; checks metadata again just before deletion.
Never deletes final images, unrelated user files, or files moved out of temp.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import re

import httpx

from app.core.config import get_settings
from app.modules.realistic_review_ugc.stage1_temp_previews import TEMP_FOLDER_ID, PREVIEW_TTL_HOURS
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.google.storage import GoogleDriveAssetStorage, _escape_query

log = logging.getLogger(__name__)
OWNED_ASSET = re.compile(
    r"^rrugc-stage1-draft:[0-9a-f-]{36}:[0-9a-f-]{36}:[0-9]{1,8}$"
)
ENDPOINT = "https://www.googleapis.com/drive/v3/files"


def eligible(metadata: dict, *, now: datetime) -> bool:
    """Fail closed unless metadata proves parent, type, ownership and age."""
    props = metadata.get("appProperties") or {}
    asset_id = str(props.get("cam_asset_id") or "")
    filename = str(metadata.get("name") or "")
    tenant = str(props.get("cam_tenant_id") or "")
    if (
        metadata.get("trashed") is not False
        or TEMP_FOLDER_ID not in (metadata.get("parents") or [])
        or metadata.get("mimeType") != "image/webp"
        or not filename.startswith("stage1_draft_") or not filename.endswith(".webp")
        or not tenant or not OWNED_ASSET.fullmatch(asset_id)
        or not metadata.get("id")
    ):
        return False
    try:
        created = datetime.fromisoformat(str(metadata["createdTime"]).replace("Z", "+00:00"))
        if created.tzinfo is None:
            return False
    except (ValueError, KeyError, TypeError):
        return False
    # Exact 48 hours from creation, not midnight on the second calendar day.
    return created <= now - timedelta(hours=PREVIEW_TTL_HOURS)


async def purge(storage: GoogleDriveAssetStorage, *, apply: bool = False,
                now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    token = await storage.get_access_token()
    summary = {"scanned": 0, "eligible": 0, "deleted": 0, "skipped": 0,
               "errors": 0, "mode": "apply" if apply else "dry-run"}
    targets: list[dict] = []
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(60, connect=10, read=60),
        transport=storage._transport,
    ) as client:
        # A query against an inaccessible folder can silently return zero
        # results; explicitly confirm the destination folder is visible.
        check = await client.get(
            f"{ENDPOINT}/{TEMP_FOLDER_ID}",
            params={"fields": "id,mimeType,trashed", "supportsAllDrives": "true"},
        )
        storage._raise_for_status(check)
        folder = check.json()
        if (folder.get("id") != TEMP_FOLDER_ID
                or folder.get("mimeType") != "application/vnd.google-apps.folder"
                or folder.get("trashed") is not False):
            raise RuntimeError("Stage 1 temp folder is unavailable or not a Drive folder")
        page = None
        while True:
            params = {
                "q": f"'{_escape_query(TEMP_FOLDER_ID)}' in parents and trashed = false",
                "fields": "nextPageToken,files(id,name,parents,createdTime,mimeType,trashed,appProperties)",
                "pageSize": "1000",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page:
                params["pageToken"] = page
            response = await client.get(ENDPOINT, params=params)
            storage._raise_for_status(response)
            body = response.json()
            for record in body.get("files") or []:
                summary["scanned"] += 1
                if eligible(record, now=now):
                    summary["eligible"] += 1
                    targets.append(record)
                else:
                    summary["skipped"] += 1
            page = body.get("nextPageToken")
            if not page:
                break
        if apply:
            for record in targets:
                try:
                    # Recheck before deleting, including folder parents and
                    # immutable ownership / creation metadata, to prevent
                    # deleting a file moved or repurposed during pagination.
                    response = await client.get(
                        f"{ENDPOINT}/{record['id']}",
                        params={
                            "fields": "id,name,parents,createdTime,mimeType,trashed,appProperties",
                            "supportsAllDrives": "true",
                        },
                    )
                    if response.status_code == 404:
                        summary["skipped"] += 1
                        continue
                    storage._raise_for_status(response)
                    refreshed = response.json()
                    if not eligible(refreshed, now=now) or refreshed.get("appProperties") != record.get("appProperties"):
                        summary["skipped"] += 1
                        continue
                    removal = await client.delete(
                        f"{ENDPOINT}/{record['id']}",
                        params={"supportsAllDrives": "true"},
                    )
                    if removal.status_code == 404:
                        summary["skipped"] += 1
                    else:
                        storage._raise_for_status(removal)
                        summary["deleted"] += 1
                except (httpx.HTTPError, ValueError) as exc:
                    summary["errors"] += 1
                    log.error("stage1_temp_delete_failed file_id=%s err=%s", record["id"], type(exc).__name__)
    log.info("stage1_temp_retention scanned=%d eligible=%d deleted=%d errors=%d mode=%s",
             summary["scanned"], summary["eligible"], summary["deleted"],
             summary["errors"], summary["mode"])
    return summary


def main() -> None:
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    storage = build_managed_storage_provider(get_settings())
    if not isinstance(storage, GoogleDriveAssetStorage):
        raise RuntimeError("Stage 1 preview retention requires Google Drive managed storage")
    result = asyncio.run(purge(storage, apply=args.apply))
    print(json.dumps(result, sort_keys=True))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
