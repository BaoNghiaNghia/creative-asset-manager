from __future__ import annotations

from urllib.parse import urlparse

import httpx

from app.domain.providers.contracts import (
    ExternalAssetCandidate,
    ListSourceChangesInput,
    SourceChange,
    SourceChangePage,
)
from app.providers.microsoft.mapper import FOLDER_MIME, make_id


def _safe_graph_cursor(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "graph.microsoft.com":
        raise ValueError("SharePoint delta cursor must target Microsoft Graph")
    return value


def _candidate(item: dict, source_id: str, drive_id: str) -> ExternalAssetCandidate:
    is_folder = "folder" in item
    file_data = item.get("file") or {}
    parent = item.get("parentReference") or {}
    hashes = file_data.get("hashes") or {}
    checksum = hashes.get("sha256Hash") or hashes.get("sha1Hash") or hashes.get("quickXorHash")
    return ExternalAssetCandidate(
        source_type="sharepoint",
        source_id=source_id,
        external_asset_id=make_id("item", drive_id, item["id"]),
        filename=item.get("name"),
        mime_type=FOLDER_MIME if is_folder else file_data.get("mimeType"),
        size_bytes=item.get("size"),
        source_created_at=item.get("createdDateTime"),
        source_modified_at=item.get("lastModifiedDateTime"),
        provider_checksum=checksum or item.get("cTag"),
        provider_version=item.get("cTag"),
        source_metadata={
            "drive_id": drive_id,
            "parent_id": parent.get("id"),
            "parent_path": parent.get("path"),
            "is_folder": is_folder,
            "web_url": item.get("webUrl"),
            "etag": item.get("eTag"),
        },
    )


async def list_sharepoint_delta(
    access_token: str, input: ListSourceChangesInput
) -> SourceChangePage:
    drive_id = str(input.source_metadata.get("drive_id") or "")
    if not drive_id:
        raise ValueError("SharePoint source metadata must contain drive_id")
    if input.cursor and not input.reconciliation:
        url = _safe_graph_cursor(input.cursor)
    else:
        url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta"
    params = None if input.cursor and not input.reconciliation else {
        "$top": str(input.page_size),
        "$select": (
            "id,name,size,createdDateTime,lastModifiedDateTime,webUrl,parentReference,"
            "file,folder,deleted,eTag,cTag"
        ),
    }
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=httpx.Timeout(25, connect=8),
    ) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    mapped: list[SourceChange] = []
    for item in data.get("value", []):
        external_id = make_id("item", drive_id, item["id"])
        deleted = "deleted" in item
        mapped.append(
            SourceChange(
                change_type="deleted" if deleted else "updated",
                external_asset_id=external_id,
                candidate=None if deleted else _candidate(item, input.source_id, drive_id),
            )
        )
    next_link = data.get("@odata.nextLink")
    delta_link = data.get("@odata.deltaLink")
    cursor = _safe_graph_cursor(next_link or delta_link) if next_link or delta_link else input.cursor
    return SourceChangePage(tuple(mapped), cursor, bool(next_link))
