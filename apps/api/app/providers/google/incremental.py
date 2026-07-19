from __future__ import annotations

import httpx

from app.domain.providers.contracts import (
    ExternalAssetCandidate,
    ListSourceChangesInput,
    SourceChange,
    SourceChangePage,
)

FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = (
    "id,name,mimeType,parents,size,createdTime,modifiedTime,trashed,"
    "md5Checksum,sha1Checksum,sha256Checksum,version,headRevisionId,webViewLink"
)


def _candidate(item: dict, source_id: str) -> ExternalAssetCandidate:
    checksum = item.get("sha256Checksum") or item.get("sha1Checksum") or item.get("md5Checksum")
    return ExternalAssetCandidate(
        source_type="google_drive",
        source_id=source_id,
        external_asset_id=item["id"],
        filename=item.get("name"),
        mime_type=item.get("mimeType"),
        size_bytes=int(item["size"]) if item.get("size") else None,
        source_created_at=item.get("createdTime"),
        source_modified_at=item.get("modifiedTime"),
        provider_checksum=checksum,
        provider_version=str(item.get("headRevisionId") or "") or None,
        source_metadata={
            "parents": item.get("parents") or [],
            "is_folder": item.get("mimeType") == FOLDER_MIME,
            "web_url": item.get("webViewLink"),
        },
    )


async def list_drive_changes(
    access_token: str, input: ListSourceChangesInput
) -> SourceChangePage:
    headers = {"Authorization": f"Bearer {access_token}"}
    timeout = httpx.Timeout(20, connect=8)
    async with httpx.AsyncClient(
        base_url="https://www.googleapis.com/drive/v3", headers=headers, timeout=timeout
    ) as client:
        if input.reconciliation:
            params: dict[str, str | int] = {
                "q": "trashed = false",
                "fields": f"nextPageToken,files({FILE_FIELDS})",
                "pageSize": input.page_size,
                "spaces": "drive",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if input.cursor:
                params["pageToken"] = input.cursor
            response = await client.get("/files", params=params)
            response.raise_for_status()
            data = response.json()
            next_cursor = data.get("nextPageToken")
            changes = tuple(
                SourceChange(
                    change_type="updated",
                    external_asset_id=item["id"],
                    candidate=_candidate(item, input.source_id),
                )
                for item in data.get("files", [])
            )
            return SourceChangePage(changes, next_cursor, bool(next_cursor))

        if input.cursor is None:
            response = await client.get(
                "/changes/startPageToken", params={"supportsAllDrives": "true"}
            )
            response.raise_for_status()
            return SourceChangePage((), response.json()["startPageToken"], False)

        response = await client.get(
            "/changes",
            params={
                "pageToken": input.cursor,
                "pageSize": input.page_size,
                "spaces": "drive",
                "includeRemoved": "true",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "fields": f"nextPageToken,newStartPageToken,changes(fileId,removed,file({FILE_FIELDS}))",
            },
        )
        response.raise_for_status()
        data = response.json()
        mapped: list[SourceChange] = []
        for change in data.get("changes", []):
            item = change.get("file") or {}
            external_id = change.get("fileId") or item.get("id")
            removed = change.get("removed") or item.get("trashed") or not item
            mapped.append(
                SourceChange(
                    change_type="deleted" if removed else "updated",
                    external_asset_id=external_id,
                    candidate=None if removed else _candidate(item, input.source_id),
                )
            )
        next_cursor = data.get("nextPageToken") or data.get("newStartPageToken") or input.cursor
        return SourceChangePage(tuple(mapped), next_cursor, bool(data.get("nextPageToken")))
