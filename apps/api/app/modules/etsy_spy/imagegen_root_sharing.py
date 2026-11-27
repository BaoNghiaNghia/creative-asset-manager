"""Explicit operator action for the single approved CAM ImageGen Drive root.

Normal uploads and OAuth reconnect never create a public permission. The
operator invokes this action separately after verifying the deployed policy.
"""
from __future__ import annotations

import httpx
from fastapi import HTTPException

from .imagegen_drive import (
    LINK_VIEWER_ROOT_ID, LINK_VIEWER_POLICY, root_link_viewer_enabled,
    link_viewer_permission, provider_and_folder, verify_imagegen_oauth_token,
)

API = "https://www.googleapis.com/drive/v3/files"
FIELDS = ("id,mimeType,trashed,ownedByMe,capabilities(canShare,canAddChildren),"
          "permissions(id,type,role,allowFileDiscovery)")


def inspect_root(data, root):
    if (data.get("id") != root or data.get("mimeType") != "application/vnd.google-apps.folder"
            or data.get("trashed") is not False or data.get("ownedByMe") is not True
            or data.get("capabilities", {}).get("canShare") is not True
            or data.get("capabilities", {}).get("canAddChildren") is not True):
        raise HTTPException(409, "ImageGen root ownership or sharing capability mismatch")
    permissions = data.get("permissions")
    if not isinstance(permissions, list) or any(not isinstance(p, dict) for p in permissions):
        raise HTTPException(409, "ImageGen root permission metadata unavailable")
    broad = [p for p in permissions if p.get("type") in {"anyone", "domain"}]
    if not broad:
        return "restricted"
    if len(broad) == 1 and link_viewer_permission(broad[0]):
        return "link_viewer"
    raise HTTPException(409, "Unexpected broad permissions; no automatic mutation")


async def ensure_root_viewer(client, root, *, apply=False):
    if root != LINK_VIEWER_ROOT_ID or not root_link_viewer_enabled():
        raise HTTPException(409, "ImageGen root is not the approved sharing target")
    async def metadata():
        r = await client.get(f"{API}/{root}", params={"supportsAllDrives": "true", "fields": FIELDS})
        if r.status_code != 200:
            raise HTTPException(503, "Google Drive root metadata unavailable")
        return inspect_root(r.json(), root)
    state = await metadata()
    if state == "link_viewer" or not apply:
        return {"policy": LINK_VIEWER_POLICY, "state": state, "changed": False}
    # Create one non-discoverable read-only grant. Never remove or replace
    # other permissions. If the result is ambiguous, stop for review.
    try:
        response = await client.post(f"{API}/{root}/permissions",
            params={"supportsAllDrives": "true", "fields": "id"},
            json={"type": "anyone", "role": "reader", "allowFileDiscovery": False})
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Drive sharing outcome uncertain; read back before retry") from exc
    if response.status_code not in (200, 201):
        raise HTTPException(502, f"Google rejected root Viewer grant (HTTP {response.status_code})")
    if await metadata() != "link_viewer":
        raise HTTPException(502, "Root Viewer grant not verified; review current ACL")
    return {"policy": LINK_VIEWER_POLICY, "state": "link_viewer", "changed": True}


async def run_root_sharing(*, apply=False):
    provider, root = provider_and_folder()
    if root != LINK_VIEWER_ROOT_ID or not root_link_viewer_enabled():
        raise HTTPException(409, "Unexpected configured ImageGen root")
    token = await provider.get_access_token()
    # OAuth account, exact owner and write access must pass before any mutation.
    await verify_imagegen_oauth_token(token, root, return_sharing_status=True)
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(20, connect=5), follow_redirects=False, trust_env=False,
    ) as client:
        result = await ensure_root_viewer(client, root, apply=apply)
    state = await verify_imagegen_oauth_token(token, root, return_sharing_status=True)
    if state != result["state"]:
        raise HTTPException(502, "Root ACL readback changed; review before retry")
    return {**result, "root_id": root}
