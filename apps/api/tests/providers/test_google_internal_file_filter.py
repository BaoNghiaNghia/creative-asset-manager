from __future__ import annotations

import unittest
from unittest.mock import patch

from app.domain.providers.contracts import ListSourceChangesInput
from app.providers.google.incremental import FILE_FIELDS, _candidate, list_drive_changes
from app.providers.google.internal_files import is_cam_managed_file


def _item(file_id: str, app_properties: dict | None = None, **extra) -> dict:
    return {
        "id": file_id,
        "name": extra.pop("name", f"{file_id}.png"),
        "mimeType": extra.pop("mimeType", "image/png"),
        "parents": extra.pop("parents", ["folder-a"]),
        "appProperties": app_properties or {},
        **extra,
    }


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _Client:
    def __init__(self, responses: list[dict], calls: list[tuple[str, dict | None]], **_):
        self._responses = responses
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, path: str, params: dict | None = None):
        self._calls.append((path, params))
        return _Response(self._responses.pop(0))


class GoogleInternalFileFilterTest(unittest.IsolatedAsyncioTestCase):
    def test_fields_and_candidate_preserve_app_properties(self) -> None:
        self.assertIn("appProperties", FILE_FIELDS)
        candidate = _candidate(
            _item("managed", {"cam_tenant_id": "t", "cam_asset_id": "a", "cam_content_hash": "h"}),
            "source-a",
        )
        self.assertEqual(candidate.source_metadata["app_properties"]["cam_asset_id"], "a")

    def test_detector_only_recognizes_cam_markers(self) -> None:
        self.assertFalse(is_cam_managed_file(_item("user", {"custom": "value"})))
        self.assertFalse(is_cam_managed_file(_item("partial", {"cam_tenant_id": "t"})))
        self.assertTrue(is_cam_managed_file(_item("managed", {
            "cam_tenant_id": "t", "cam_asset_id": "a", "cam_content_hash": "h"
        })))
        self.assertTrue(is_cam_managed_file(_item("sidecar", {"cam_sidecar": "metadata-v1"})))

    async def test_reconciliation_ignores_managed_binary_and_sidecar_but_keeps_normal_items(self) -> None:
        calls: list[tuple[str, dict | None]] = []
        responses = [{
            "files": [
                _item("normal"),
                _item("unrelated", {"custom": "value"}),
                _item("folder", {}, mimeType="application/vnd.google-apps.folder"),
                _item("managed", {"cam_tenant_id": "t", "cam_asset_id": "a", "cam_content_hash": "h"}),
                _item("sidecar", {"cam_sidecar": "metadata-v1"}),
            ]
        }]
        with patch(
            "app.providers.google.incremental.httpx.AsyncClient",
            lambda **kwargs: _Client(responses, calls, **kwargs),
        ):
            page = await list_drive_changes(
                "token-not-logged",
                ListSourceChangesInput("source-a", reconciliation=True),
            )
        by_id = {change.external_asset_id: change for change in page.changes}
        self.assertEqual(by_id["normal"].change_type, "updated")
        self.assertEqual(by_id["unrelated"].change_type, "updated")
        self.assertTrue(by_id["folder"].candidate.source_metadata["is_folder"])
        self.assertEqual(by_id["managed"].change_type, "deleted")
        self.assertEqual(by_id["sidecar"].change_type, "deleted")
        self.assertIn("appProperties", str(calls[0][1]["fields"]))
        self.assertEqual(calls[0][1]["supportsAllDrives"], "true")
        self.assertEqual(calls[0][1]["includeItemsFromAllDrives"], "true")

    async def test_incremental_internal_update_uses_safe_delete_transition(self) -> None:
        calls: list[tuple[str, dict | None]] = []
        responses = [{
            "newStartPageToken": "cursor-next",
            "changes": [
                {"fileId": "normal", "removed": False, "file": _item("normal")},
                {"fileId": "managed", "removed": False, "file": _item("managed", {
                    "cam_tenant_id": "t", "cam_asset_id": "a", "cam_content_hash": "h"
                })},
                {"fileId": "sidecar", "removed": False, "file": _item("sidecar", {"cam_sidecar": "metadata-v1"})},
                {"fileId": "removed", "removed": True},
            ],
        }]
        with patch(
            "app.providers.google.incremental.httpx.AsyncClient",
            lambda **kwargs: _Client(responses, calls, **kwargs),
        ):
            page = await list_drive_changes(
                "token-not-logged",
                ListSourceChangesInput("source-a", cursor="cursor-before"),
            )
        by_id = {change.external_asset_id: change for change in page.changes}
        self.assertEqual(by_id["normal"].change_type, "updated")
        self.assertEqual(by_id["managed"].change_type, "deleted")
        self.assertEqual(by_id["sidecar"].change_type, "deleted")
        self.assertEqual(by_id["removed"].change_type, "deleted")
        self.assertEqual(page.next_cursor, "cursor-next")
        self.assertIn("appProperties", str(calls[0][1]["fields"]))
