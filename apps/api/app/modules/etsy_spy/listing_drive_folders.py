"""Idempotent, owner-scoped Google Drive folder hierarchy for ImageGen packs.

Root CAM ImageGen Designs -> listing_<ID> -> design_<SHA16>_<placement>
 -> <C1717|G500|G180|G185> -> thumbnails.

All operations use CAM's verified managed-OAuth token. Never make the output
gallery public; a browser reads images exclusively through authenticated CAM.
"""
from __future__ import annotations

import re
import httpx
from fastapi import HTTPException

ROOT_FOLDER_ID = "1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl"
_DRIVE = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_LISTING = re.compile(r"^[0-9]{5,20}$")
_PLACEMENT = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_GARMENTS = {"C1717", "G500", "G180", "G185"}
_FOLDER_MIME = "application/vnd.google-apps.folder"
_API = "https://www.googleapis.com/drive/v3/files"


def _validate_inputs(root, listing, design_sha, placement, garment):
    if (root != ROOT_FOLDER_ID or not _LISTING.fullmatch(listing) or
        not _SHA.fullmatch(design_sha) or
        not _PLACEMENT.fullmatch(placement) or garment not in _GARMENTS):
        raise HTTPException(422, "Invalid ImageGen listing/design/target garment identity")


async def _ensure_child(client: httpx.AsyncClient, parent: str, name: str,
                        props: dict[str, str]) -> str:
    if not _DRIVE.fullmatch(parent) or not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", name):
        raise HTTPException(422, "Invalid ImageGen Drive child identity")
    # All inputs are validated to safe fixed alphabets; Drive q needs no user
    # interpolation beyond these exact names and IDs.
    query = (
        f"'{parent}' in parents and trashed = false and "
        f"mimeType = '{_FOLDER_MIME}' and name = '{name}'"
    )
    params = {
        "q": query, "pageSize": "3",
        "fields": "nextPageToken,files(id,name,parents,mimeType,appProperties)",
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
    }
    try:
        response = await client.get(_API, params=params)
        response.raise_for_status()
        payload = response.json()
        found = payload.get("files") or []
        if len(found) > 1 or payload.get("nextPageToken"):
            raise HTTPException(409, "Ambiguous ImageGen Drive folder identity")
        if found:
            child = found[0]
        else:
            created = await client.post(_API, params={
                "fields": "id,name,parents,mimeType,appProperties",
                "supportsAllDrives": "true",
            }, json={"name": name, "mimeType": _FOLDER_MIME,
                     "parents": [parent], "appProperties": props})
            created.raise_for_status()
            child = created.json()
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(503, "ImageGen Drive subfolder unavailable") from exc
    child_id = str(child.get("id") or "")
    child_props = child.get("appProperties") or {}
    if (not _DRIVE.fullmatch(child_id) or child.get("name") != name or
        child.get("mimeType") != _FOLDER_MIME or
        parent not in (child.get("parents") or []) or
        not isinstance(child_props, dict) or
        any(child_props.get(k) != v for k, v in props.items())):
        raise HTTPException(409, "ImageGen Drive folder provenance mismatch")
    return child_id



async def verify_listing_file_provenance(
    *, provider, remote_file_id: str, folder_id: str, tenant_id: str,
    asset_id: str, sha256: str, transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Read back exact SHA, parent, tenant and asset identity after upload.

    The generic managed provider discovers saved files by asset ID, so a
    corrupted or conflicting re-upload must never silently inherit old bytes.
    """
    if (not _DRIVE.fullmatch(remote_file_id) or
        not _DRIVE.fullmatch(folder_id) or not _SHA.fullmatch(sha256)):
        raise HTTPException(422, "Invalid saved Drive file proof")
    token = await provider.get_access_token()
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(25, connect=8), transport=transport,
    ) as client:
        try:
            response = await client.get(
                f"{_API}/{remote_file_id}", params={
                    "supportsAllDrives": "true",
                    "fields": "id,parents,trashed,appProperties",
                },
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise HTTPException(503, "ImageGen Drive saved file proof unavailable") from exc
    props = data.get("appProperties") or {}
    if (data.get("id") != remote_file_id or data.get("trashed") or
        folder_id not in (data.get("parents") or []) or
        props.get("cam_tenant_id") != tenant_id or
        props.get("cam_asset_id") != asset_id or
        props.get("cam_content_hash") != sha256):
        raise HTTPException(409, "Drive output SHA, tenant or folder proof mismatch")

async def ensure_listing_output_folders(
    *, provider, root_folder_id: str, tenant_id: str,
    listing_id: str, design_sha256: str, placement: str,
    garment_code: str, transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, str]:
    """Return stable private Drive folders after exact folder provenance checks.

    Caller must first verify the managed OAuth owner and root folder sharing.
    """
    _validate_inputs(root_folder_id, listing_id, design_sha256,
                     placement, garment_code)
    if not tenant_id or len(tenant_id) > 255:
        raise HTTPException(422, "ImageGen tenant identity required")
    token = await provider.get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(
        headers=headers, timeout=httpx.Timeout(30, connect=8),
        transport=transport,
    ) as client:
        listing = await _ensure_child(
            client, root_folder_id, "listing_" + listing_id,
            {"cam_imagegen_node": "listing-v1", "cam_tenant": tenant_id,
             "cam_listing": listing_id},
        )
        design = await _ensure_child(
            client, listing,
            "design_" + design_sha256[:16] + "_" + placement,
            {"cam_imagegen_node": "design-v1", "cam_tenant": tenant_id,
             "cam_listing": listing_id, "cam_design_sha": design_sha256,
             "cam_placement": placement},
        )
        garment = await _ensure_child(
            client, design, garment_code,
            {"cam_imagegen_node": "garment-v1", "cam_tenant": tenant_id,
             "cam_listing": listing_id, "cam_design_sha": design_sha256,
             "cam_target": garment_code},
        )
        thumbnails = await _ensure_child(
            client, garment, "thumbnails",
            {"cam_imagegen_node": "thumbnail-v1", "cam_tenant": tenant_id,
             "cam_listing": listing_id, "cam_design_sha": design_sha256,
             "cam_target": garment_code},
        )
    return {"listing_folder_id": listing, "design_folder_id": design,
            "garment_folder_id": garment, "thumbnail_folder_id": thumbnails,
            "relative_path": (
                f"listing_{listing_id}/design_{design_sha256[:16]}_{placement}/"
                f"{garment_code}"
            )}
