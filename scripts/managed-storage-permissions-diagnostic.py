from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

import httpx
from app.core.config import get_settings
from app.modules.storage.managed_oauth import (
    GOOGLE_TOKEN_URI, resolve_managed_storage_credential, managed_storage_oauth_status,
)
from app.providers.google.auth import DRIVE_WRITE_SCOPE


async def main():
    settings = get_settings()
    status = managed_storage_oauth_status(settings)
    token = resolve_managed_storage_credential(settings).refresh_token
    folder_id = str(settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID or "").strip()
    print(
        "CONFIG",
        f"enabled={bool(settings.MANAGED_ASSET_STORAGE_ENABLED)}",
        f"connected_record={bool(status.get('connected'))}",
        f"reconnect_record={bool(status.get('reconnect_required'))}",
        f"root_configured={bool(folder_id)}",
        f"token_present={bool(token)}",
        flush=True,
    )
    if not token or not folder_id:
        return
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=8)) as client:
            response = await client.post(
                GOOGLE_TOKEN_URI,
                data={
                    "client_id": str(settings.GOOGLE_CLIENT_ID or "").strip(),
                    "client_secret": str(settings.GOOGLE_CLIENT_SECRET or "").strip(),
                    "refresh_token": token,
                    "grant_type": "refresh_token",
                },
            )
            print("TOKEN", f"http={response.status_code}", flush=True)
            if response.status_code != 200:
                data = response.json()
                print("TOKEN_ERROR", str(data.get("error") or "unknown")[:50], flush=True)
                return
            data = response.json()
            access = data.get("access_token")
            scopes = str(data.get("scope") or "").split()
            print("TOKEN_SCOPE", f"drive_write={DRIVE_WRITE_SCOPE in scopes}", f"scopes_returned={bool(scopes)}", flush=True)
            headers = {"Authorization": f"Bearer {access}"}
            about = await client.get(
                "https://www.googleapis.com/drive/v3/about",
                params={"fields": "user(permissionId,emailAddress)"},
                headers=headers,
            )
            print("ABOUT", f"http={about.status_code}", flush=True)
            if about.status_code == 200:
                user = (about.json().get("user") or {})
                print("ACCOUNT", f"permission_id_present={bool(user.get('permissionId'))}", f"email_present={bool(user.get('emailAddress'))}", flush=True)
            folder = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{folder_id}",
                params={
                    "fields": "id,mimeType,trashed,driveId,ownedByMe,capabilities(canAddChildren,canDeleteChildren,canTrashChildren,canReadRevisions,canRename,canShare),owners(emailAddress),permissionIds",
                    "supportsAllDrives": "true",
                },
                headers=headers,
            )
            print("FOLDER", f"http={folder.status_code}", flush=True)
            if folder.status_code == 200:
                data = folder.json()
                caps = data.get("capabilities") or {}
                print(
                    "FOLDER_FLAGS",
                    f"is_folder={data.get('mimeType') == 'application/vnd.google-apps.folder'}",
                    f"trashed={bool(data.get('trashed'))}",
                    f"shared_drive={bool(data.get('driveId'))}",
                    f"owned_by_me={bool(data.get('ownedByMe'))}",
                    " ".join(f"{name}={caps.get(name)}" for name in (
                        "canAddChildren", "canDeleteChildren", "canTrashChildren", "canRename", "canShare"
                    )),
                    flush=True,
                )
            else:
                data = folder.json()
                error = (data.get("error") or {})
                print("FOLDER_ERROR", f"code={error.get('code')}", "reason=" + str((error.get("errors") or [{}])[0].get("reason") or "unknown")[:60], flush=True)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        print("DIAGNOSTIC_FAILED", type(exc).__name__, flush=True)

if __name__ == "__main__":
    asyncio.run(main())
