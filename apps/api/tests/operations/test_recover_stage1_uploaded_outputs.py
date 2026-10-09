from __future__ import annotations

import asyncio

import httpx
import pytest

from app.operations.recover_stage1_uploaded_outputs import find_stored_output


def test_recover_query_identifies_exact_tenant_job_and_attempt():
    seen = []

    def responder(request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        seen.append(query)
        if "rrugc-keyword-image:job-1:attempt-1:0" not in query:
            return httpx.Response(200, json={"files": []})
        return httpx.Response(200, json={"files": [{
            "id": "stored-a", "mimeType": "image/png", "size": "123",
            "imageMediaMetadata": {"width": 12, "height": 34},
            "appProperties": {
                "cam_tenant_id": "tenant-a",
                "cam_asset_id": "rrugc-keyword-image:job-1:attempt-1:0",
            },
        }]})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            found = await find_stored_output(
                client, folder_id="root", tenant_id="tenant-a",
                job_id="job-1", processing_job_id="attempt-1", index=0,
            )
            absent = await find_stored_output(
                client, folder_id="root", tenant_id="tenant-a",
                job_id="job-1", processing_job_id="attempt-1", index=1,
            )
        assert found["id"] == "stored-a"
        assert absent is None

    asyncio.run(exercise())
    assert "cam_tenant_id" in seen[0]
    assert "cam_asset_id" in seen[0]


@pytest.mark.parametrize("change", [
    {"appProperties": {"cam_tenant_id": "tenant-b", "cam_asset_id": "rrugc-keyword-image:job-1:attempt-1:0"}},
    {"mimeType": "application/pdf"},
    {"size": ""},
])
def test_recover_rejects_invalid_or_other_tenant_objects(change):
    item = {
        "id": "file-1", "mimeType": "image/png", "size": "123",
        "appProperties": {
            "cam_tenant_id": "tenant-a",
            "cam_asset_id": "rrugc-keyword-image:job-1:attempt-1:0",
        },
    }
    item.update(change)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"files": [item]})
        )) as client:
            await find_stored_output(
                client, folder_id="root", tenant_id="tenant-a",
                job_id="job-1", processing_job_id="attempt-1", index=0,
            )
    with pytest.raises(RuntimeError):
        asyncio.run(exercise())
