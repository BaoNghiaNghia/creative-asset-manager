import httpx
import pytest

from app.modules.inventory.daily_sheet.google_client import (
    DailySheetAuthorizationError,
    DailySheetRateLimited,
    GoogleSheetsInventoryClient,
)


def test_disposable_google_writeback_only_mutates_and_trashes_a_new_copy():
    from urllib.parse import unquote
    import json

    requests = []
    observed_marker = {"value": None}
    def handler(request):
        requests.append(request)
        path = unquote(request.url.path)
        if path == "/drive/v3/files/source" and request.method == "GET":
            return httpx.Response(200, json={"id": "source", "name": "Source", "mimeType": "application/vnd.google-apps.spreadsheet"})
        if path == "/drive/v3/files/source/copy" and request.method == "POST":
            body = json.loads(request.content)
            assert body["parents"] == ["disposable-folder"]
            assert body["appProperties"]["cam_inventory_qa_disposable"] == "true"
            return httpx.Response(200, json={"id": "disposable-qa", "mimeType": "application/vnd.google-apps.spreadsheet"})
        if path == "/v4/spreadsheets/disposable-qa:batchUpdate":
            body = json.loads(request.content)
            assert body["requests"][0]["addSheet"]["properties"]["hidden"] is True
            return httpx.Response(200, json={"replies": [{"addSheet": {"properties": {"sheetId": 99}}}]})
        if path == "/v4/spreadsheets/disposable-qa/values:batchUpdate":
            observed_marker["value"] = json.loads(request.content)["data"][0]["values"][0][0]
            assert observed_marker["value"].startswith("CAM-QA-")
            return httpx.Response(200, json={})
        if path == "/v4/spreadsheets/disposable-qa/values:batchGet":
            return httpx.Response(200, json={"valueRanges": [{"values": [[observed_marker["value"]]]}]})
        if path == "/drive/v3/files/disposable-qa" and request.method == "PATCH":
            assert json.loads(request.content) == {"trashed": True}
            return httpx.Response(200, json={"id": "disposable-qa", "trashed": True})
        return httpx.Response(500, json={"unexpected_path": path})

    client = GoogleSheetsInventoryClient(
        "token", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None,
    )
    result = client.verify_disposable_writeback(
        "source", folder_id="disposable-folder", tenant_id="tenant-a",
    )
    assert result["verified"] is True
    assert result["disposable_copy_trashed"] is True
    assert result["source_file_unchanged_by_qa"] is True
    writes = [req for req in requests if req.method in {"POST", "PATCH"}]
    assert len(writes) == 4
    assert all("/source/" not in unquote(req.url.path) or req.url.path.endswith("/copy") for req in writes)
    assert not any("/source/values" in str(req.url) for req in writes)


def test_disposable_writeback_without_archive_uses_source_parent_and_never_source():
    import json
    copied_to = []
    def handler(request):
        path = request.url.path
        if path.endswith("/source") and request.method == "GET":
            return httpx.Response(200, json={
                "id": "source", "mimeType": "application/vnd.google-apps.spreadsheet",
                "parents": ["same-parent"],
            })
        if path.endswith("/source/copy"):
            copied_to.extend(json.loads(request.content)["parents"])
            return httpx.Response(200, json={"id": "qa-copy"})
        if path.endswith("/qa-copy:batchUpdate"):
            return httpx.Response(200, json={})
        if path.endswith("values:batchUpdate"):
            import json as _json
            handler.marker = _json.loads(request.content)["data"][0]["values"][0][0]
            return httpx.Response(200, json={})
        if path.endswith("values:batchGet"):
            return httpx.Response(200, json={
                "valueRanges": [{"values": [[handler.marker]]}],
            })
        if path.endswith("/qa-copy") and request.method == "PATCH":
            return httpx.Response(200, json={})
        return httpx.Response(500)
    client = GoogleSheetsInventoryClient(
        "token", client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.verify_disposable_writeback("source", folder_id=None, tenant_id="tenant-a")
    assert result["verified"] and result["disposable_copy_trashed"]
    assert copied_to == ["same-parent"]


def test_disposable_google_writeback_trashes_copy_when_readback_fails():
    import json
    trashed = []
    def handler(request):
        path = request.url.path
        if path.endswith("/source") and request.method == "GET":
            return httpx.Response(200, json={"id": "source", "mimeType": "application/vnd.google-apps.spreadsheet"})
        if path.endswith("/source/copy"):
            return httpx.Response(200, json={"id": "disposable-qa"})
        if path.endswith(":batchUpdate"):
            return httpx.Response(200, json={})
        if path.endswith("values:batchUpdate"):
            return httpx.Response(200, json={})
        if path.endswith("values:batchGet"):
            return httpx.Response(200, json={"valueRanges": [{"values": [["wrong"]]}]})
        if path.endswith("/disposable-qa") and request.method == "PATCH":
            trashed.append(json.loads(request.content))
            return httpx.Response(200, json={})
        return httpx.Response(500)
    client = GoogleSheetsInventoryClient("token", client=httpx.Client(transport=httpx.MockTransport(handler)), max_attempts=1)
    with pytest.raises(RuntimeError, match="disposable_writeback_readback_mismatch"):
        client.verify_disposable_writeback("source", folder_id="disposable-folder", tenant_id="tenant-a")
    assert trashed == [{"trashed": True}]


def test_google_client_retries_rate_limit_with_retry_after():
    calls = []
    sleeps = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {}})
        return httpx.Response(200, json={"spreadsheetId": "sheet"})
    client = GoogleSheetsInventoryClient("token", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append)
    assert client.spreadsheet_metadata("sheet")["spreadsheetId"] == "sheet"
    assert len(calls) == 2
    assert sleeps == [0.0]


def test_google_client_stops_after_bounded_rate_limit_retries():
    client = GoogleSheetsInventoryClient(
        "token",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429, json={}))),
        max_attempts=2,
        sleep=lambda _: None,
    )
    with pytest.raises(DailySheetRateLimited):
        client.spreadsheet_metadata("sheet")


def test_google_client_maps_authorization_without_leaking_response():
    client = GoogleSheetsInventoryClient(
        "token",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403, text="secret"))),
    )
    with pytest.raises(DailySheetAuthorizationError, match="reconnect"):
        client.drive_file("sheet")


def test_copy_reuses_existing_snapshot_before_creating_another():
    calls = []
    def handler(request):
        calls.append((request.method, str(request.url)))
        return httpx.Response(200, json={"files": [{"id": "existing-copy"}]})
    client = GoogleSheetsInventoryClient("token", client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = client.copy_spreadsheet("source", folder_id="folder", name="Snapshot", tenant_id="tenant-a", business_date="2030-08-08")
    assert result["id"] == "existing-copy"
    assert [method for method, _ in calls] == ["GET"]


def test_copy_key_creates_a_distinct_idempotency_namespace():
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == "GET":
            assert "cam_inventory_copy_key" in str(request.url)
            assert "historical-replay" in str(request.url)
            return httpx.Response(200, json={"files": []})
        body = __import__("json").loads(request.content)
        assert body["appProperties"]["cam_inventory_copy_key"] == "historical-replay"
        return httpx.Response(200, json={"id": "fresh-replay"})
    client = GoogleSheetsInventoryClient(
        "token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.copy_spreadsheet(
        "source",
        folder_id="folder",
        name="Replay",
        tenant_id="tenant-a",
        business_date="2030-08-08",
        copy_key="historical-replay",
    )
    assert result["id"] == "fresh-replay"
    assert [request.method for request in calls] == ["GET", "POST"]
