"""Output History downloads preserve exact originals and dates without mixing tenants."""
import asyncio
from datetime import datetime, timezone
import io
import zipfile

import pytest

from app.domain.providers.contracts import StoredAssetReadStream
from app.modules.realistic_review_ugc.output_downloads import (
    OutputArchiveError, archive_filename, build_output_archive,
    output_archive_entry, output_download_name,
)


def row(version: int, name: str = "v6-output/final/design_06.png") -> dict:
    return {
        "version": version, "output_name": name,
        "remote_file_id": f"drive-{version}",
        "content_type": "image/png", "size_bytes": 100,
        "created_at": datetime(2026, 10, 12, 1, 45, tzinfo=timezone.utc),
    }


def test_archive_names_and_date_groups_are_safe_and_unique():
    assert output_download_name(row(6), "stage1") == "design_06.png"
    assert output_download_name(row(1, "../../old\\bad name.png"), "stage1") == "bad_name.png"
    assert output_archive_entry(row(6), "stage1") == "2026-10-12/v006_design_06.png"
    assert output_download_name({"version": 5}, "stage1") == "stage1_v005.png"
    assert archive_filename("stage1", "Born Broke Die Rich",
                            datetime(2026, 10, 12, tzinfo=timezone.utc)) == "stage1_Born-Broke-Die-Rich_2026-10-12.zip"


def test_archive_contains_every_original_without_thumbnails():
    original = {i: (b"PNG-original-" + str(i).encode()) * 120 for i in range(1, 7)}
    calls = []
    class Storage:
        async def open_asset(self, request):
            calls.append((request.tenant_id, request.remote_file_id))
            i = int(request.remote_file_id.rsplit("-", 1)[1])
            async def body():
                yield original[i][:50]
                yield original[i][50:]
            async def close():
                pass
            return StoredAssetReadStream(body=body(), close=close, content_type="image/png")

    async def generate():
        return await build_output_archive(Storage(), tenant_id="tenant-a",
                                           stage="stage1", job_id="job-1",
                                           rows=[row(i, f"output/final/design_{i:02d}.png") for i in range(6, 0, -1)])
    archive = asyncio.run(generate())
    try:
        with zipfile.ZipFile(archive, "r") as zipped:
            assert zipped.testzip() is None
            assert zipped.namelist() == [
                f"2026-10-12/v{i:03d}_design_{i:02d}.png" for i in range(6, 0, -1)
            ]
            for i in range(1, 7):
                assert zipped.read(f"2026-10-12/v{i:03d}_design_{i:02d}.png") == original[i]
    finally:
        archive.close()
    assert calls == [("tenant-a", f"drive-{i}") for i in range(6, 0, -1)]


def test_too_many_files_rejected_before_remote_requests():
    class Storage:
        async def open_asset(self, *_):
            raise AssertionError("Should not fetch")
    with pytest.raises(OutputArchiveError, match="Too many"):
        asyncio.run(build_output_archive(Storage(), tenant_id="tenant-a",
                    stage="stage1", job_id="job-1", rows=[row(i) for i in range(201)]))
    with pytest.raises(OutputArchiveError, match="No completed"):
        asyncio.run(build_output_archive(Storage(), tenant_id="tenant-a",
                    stage="stage1", job_id="job-1", rows=[]))


def test_archive_failure_is_not_silently_converted_to_success():
    class Storage:
        async def open_asset(self, request):
            if request.remote_file_id == "drive-2":
                raise RuntimeError("Drive unavailable")
            async def body():
                yield b"full image"
            async def close():
                pass
            return StoredAssetReadStream(body=body(), close=close)
    with pytest.raises(RuntimeError, match="Drive unavailable"):
        asyncio.run(build_output_archive(Storage(), tenant_id="tenant-a",
                    stage="stage1", job_id="job-1", rows=[row(1), row(2)]))
