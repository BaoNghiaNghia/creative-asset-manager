import httpx
import pytest

from app.modules.inventory.daily_sheet.google_client import (
    DailySheetAuthorizationError,
    DailySheetRateLimited,
    GoogleSheetsInventoryClient,
)


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
