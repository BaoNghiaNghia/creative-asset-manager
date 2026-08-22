from __future__ import annotations

import email.utils
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

NATIVE_SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
GOOGLE_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


class DailySheetProviderError(RuntimeError):
    code = "permanent_provider_error"


class DailySheetAuthorizationError(DailySheetProviderError):
    code = "authorization"


class DailySheetScopeMissing(DailySheetProviderError):
    code = "scope_missing"


class DailySheetNotFound(DailySheetProviderError):
    code = "not_found"


class DailySheetInvalidFileType(DailySheetProviderError):
    code = "invalid_file_type"


class DailySheetRateLimited(DailySheetProviderError):
    code = "rate_limited"


class DailySheetTransientError(DailySheetProviderError):
    code = "transient_provider_error"


def require_sheets_scope(scopes: list[str] | tuple[str, ...]) -> None:
    if GOOGLE_SHEETS_SCOPE not in set(scopes):
        raise DailySheetScopeMissing("Reconnect Google account and approve Google Sheets access.")


class GoogleSheetsInventoryClient:
    DRIVE = "https://www.googleapis.com/drive/v3"
    SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(
        self,
        access_token: str,
        *,
        client: httpx.Client | None = None,
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._client = client or httpx.Client(
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        self._owns_client = client is None
        self.max_attempts = max_attempts
        self.sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def _retry_after(self, response: httpx.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After")
        if value:
            try:
                return min(float(value), 30.0)
            except ValueError:
                try:
                    parsed = email.utils.parsedate_to_datetime(value)
                    return max(0.0, min((parsed - datetime.now(timezone.utc)).total_seconds(), 30.0))
                except (TypeError, ValueError):
                    pass
        return min(0.5 * (2 ** attempt), 8.0)

    def _request(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        for attempt in range(self.max_attempts):
            try:
                response = self._client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt + 1 == self.max_attempts:
                    raise DailySheetTransientError("Google service is temporarily unavailable.") from exc
                self.sleep(min(0.5 * (2 ** attempt), 8.0))
                continue
            if response.status_code in (401, 403):
                raise DailySheetAuthorizationError("Google authorization is unavailable; reconnect the account.")
            if response.status_code == 404:
                raise DailySheetNotFound("Configured Google Drive file or folder was not found.")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < self.max_attempts:
                    self.sleep(self._retry_after(response, attempt))
                    continue
                error = DailySheetRateLimited if response.status_code == 429 else DailySheetTransientError
                raise error("Google service did not become available after bounded retries.")
            if response.status_code >= 400:
                raise DailySheetProviderError("Google rejected the configured Inventory operation.")
            if not response.content:
                return {}
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        raise DailySheetTransientError("Google service is temporarily unavailable.")

    def drive_file(self, file_id: str) -> dict[str, Any]:
        return self._request("GET", f"{self.DRIVE}/files/{file_id}", params={
            "fields": "id,name,mimeType,modifiedTime,parents,webViewLink,appProperties,capabilities(canAddChildren,canEdit)",
            "supportsAllDrives": "true",
        })

    def validate_native_spreadsheet(self, file_id: str) -> dict[str, Any]:
        metadata = self.drive_file(file_id)
        if metadata.get("mimeType") != NATIVE_SPREADSHEET_MIME:
            raise DailySheetInvalidFileType(
                "The operational workbook must be converted to a native Google Sheet."
            )
        return metadata

    def ensure_archive_folder(self, root_id: str, *, tenant_id: str, business_date: str) -> dict[str, Any]:
        escaped_tenant = tenant_id.replace("'", "\\'")
        escaped_date = business_date.replace("'", "\\'")
        query = (
            f"'{root_id}' in parents and trashed=false and "
            "mimeType='application/vnd.google-apps.folder' and "
            f"appProperties has {{ key='cam_inventory_tenant_id' and value='{escaped_tenant}' }} and "
            f"appProperties has {{ key='cam_inventory_business_date' and value='{escaped_date}' }}"
        )
        found = self._request("GET", f"{self.DRIVE}/files", params={
            "q": query, "fields": "files(id,name,webViewLink,appProperties)",
            "pageSize": 2, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        }).get("files", [])
        if found:
            return found[0]
        return self._request("POST", f"{self.DRIVE}/files", params={"fields": "id,name,webViewLink,appProperties", "supportsAllDrives": "true"}, json={
            "name": business_date,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [root_id],
            "appProperties": {
                "cam_inventory_snapshot": "true",
                "cam_inventory_business_date": business_date,
                "cam_inventory_tenant_id": tenant_id,
            },
        })

    def copy_spreadsheet(self, source_id: str, *, folder_id: str, name: str, tenant_id: str, business_date: str) -> dict[str, Any]:
        escaped_tenant = tenant_id.replace("'", "\\'")
        escaped_date = business_date.replace("'", "\\'")
        escaped_source = source_id.replace("'", "\\'")
        query = (
            f"'{folder_id}' in parents and trashed=false and "
            f"mimeType='{NATIVE_SPREADSHEET_MIME}' and "
            f"appProperties has {{ key='cam_inventory_tenant_id' and value='{escaped_tenant}' }} and "
            f"appProperties has {{ key='cam_inventory_business_date' and value='{escaped_date}' }} and "
            f"appProperties has {{ key='cam_inventory_source_id' and value='{escaped_source}' }}"
        )
        found = self._request("GET", f"{self.DRIVE}/files", params={
            "q": query, "fields": "files(id,name,mimeType,modifiedTime,webViewLink,appProperties)",
            "pageSize": 2, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        }).get("files", [])
        if found:
            return found[0]
        return self._request("POST", f"{self.DRIVE}/files/{source_id}/copy", params={
            "fields": "id,name,mimeType,modifiedTime,webViewLink,appProperties",
            "supportsAllDrives": "true",
        }, json={
            "name": name, "parents": [folder_id],
            "appProperties": {
                "cam_inventory_snapshot": "true",
                "cam_inventory_business_date": business_date,
                "cam_inventory_tenant_id": tenant_id,
                "cam_inventory_source_id": source_id,
            },
        })

    def spreadsheet_metadata(self, spreadsheet_id: str) -> dict[str, Any]:
        return self._request("GET", f"{self.SHEETS}/{spreadsheet_id}", params={
            "fields": "spreadsheetId,properties.title,sheets.properties",
        })

    def batch_get_values(self, spreadsheet_id: str, ranges: list[str], *, value_render_option: str = "UNFORMATTED_VALUE") -> list[dict[str, Any]]:
        payload = self._request("GET", f"{self.SHEETS}/{spreadsheet_id}/values:batchGet", params=[
            ("ranges", item) for item in ranges
        ] + [("valueRenderOption", value_render_option), ("dateTimeRenderOption", "FORMATTED_STRING")])
        return list(payload.get("valueRanges") or [])

    def batch_update_values(self, spreadsheet_id: str, updates: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", f"{self.SHEETS}/{spreadsheet_id}/values:batchUpdate", json={
            "valueInputOption": "USER_ENTERED", "includeValuesInResponse": False, "data": updates,
        })

    def batch_clear_values(self, spreadsheet_id: str, ranges: list[str]) -> dict[str, Any]:
        return self._request("POST", f"{self.SHEETS}/{spreadsheet_id}/values:batchClear", json={"ranges": ranges})
