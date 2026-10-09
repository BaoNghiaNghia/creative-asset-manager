"""Recover Stage 1 Google Drive uploads after a version-history DB rollback.

Read-only by default. This never runs a Skill again and never touches Drive
files. --apply only writes history after confirming tenant/job/attempt identity.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.realistic_review_ugc.keyword_images import JOB_TYPE
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordImageJobModel,
)
from app.modules.realistic_review_ugc.output_versions import save_output_version
from app.modules.processing.model import ProcessingJobModel
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.google.storage import GoogleDriveAssetStorage, _escape_query

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
EXTENSION = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


async def find_stored_output(
    client: httpx.AsyncClient, *, folder_id: str, tenant_id: str,
    job_id: str, processing_job_id: str, index: int,
) -> dict | None:
    asset_id = f"rrugc-keyword-image:{job_id}:{processing_job_id}:{index}"
    query = (
        f"'{_escape_query(folder_id)}' in parents and trashed = false and "
        f"appProperties has {{ key='cam_tenant_id' and value='{_escape_query(tenant_id)}' }} and "
        f"appProperties has {{ key='cam_asset_id' and value='{_escape_query(asset_id)}' }}"
    )
    response = await client.get(
        "https://www.googleapis.com/drive/v3/files",
        params={
            "q": query, "spaces": "drive", "pageSize": "2",
            "fields": "nextPageToken,files(id,name,mimeType,size,webViewLink,imageMediaMetadata(width,height),appProperties)",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        },
    )
    response.raise_for_status()
    files = response.json().get("files") or []
    if len(files) > 1:
        raise RuntimeError(f"Ambiguous managed file for output index {index}")
    if not files:
        return None
    result = files[0]
    props = result.get("appProperties") or {}
    if props.get("cam_asset_id") != asset_id or props.get("cam_tenant_id") != tenant_id:
        raise RuntimeError("Drive appProperties do not match requested job/tenant")
    if result.get("mimeType") not in ALLOWED_MIME:
        raise RuntimeError(f"Non-image managed output at index {index}")
    if not result.get("id") or not str(result.get("size") or "").isdigit():
        raise RuntimeError(f"Managed image metadata incomplete at index {index}")
    return result


async def collect_outputs(storage: GoogleDriveAssetStorage, tenant_id: str, job_id: str,
                          processing_job_id: str, *, max_files: int = 500) -> list[dict]:
    token = await storage.get_access_token()
    found: list[dict] = []
    consecutive_gaps = 0
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(30, connect=10, read=30),
    ) as client:
        for index in range(max_files + 3):
            record = await find_stored_output(
                client, folder_id=storage._root_folder_id, tenant_id=tenant_id,
                job_id=job_id, processing_job_id=processing_job_id, index=index,
            )
            if record is None:
                consecutive_gaps += 1
                if consecutive_gaps >= 3:
                    break
                continue
            if consecutive_gaps:
                # A missing index means the upload set is incomplete. Do not
                # infer successful recovery from an interrupted/partial set.
                raise RuntimeError("Non-contiguous image indices; recovery requires manual review")
            consecutive_gaps = 0
            found.append(record)
            if len(found) >= max_files:
                raise RuntimeError("Reached recovery scan limit; increase --max-files")
    return found


async def recover(job_id: str, *, apply: bool, max_files: int) -> int:
    with SessionLocal() as session:
        job = session.scalar(select(RrugcKeywordImageJobModel).where(
            RrugcKeywordImageJobModel.id == job_id
        ))
        if job is None:
            raise RuntimeError("Stage 1 job not found")
        if job.status != "failed" or job.last_error_code != "keyword_image_internal_error":
            raise RuntimeError("Job is not an eligible failed Stage 1 DB write")
        processing = session.get(ProcessingJobModel, job.processing_job_id)
        if processing is None or processing.job_type != JOB_TYPE or processing.entity_id != job.id or processing.tenant_id != job.tenant_id:
            raise RuntimeError("Processing attempt identity mismatch")
        if processing.status != "failed":
            raise RuntimeError("Processing attempt is not terminal")
        existing = list(session.scalars(select(RrugcImageOutputVersionModel).where(
            RrugcImageOutputVersionModel.tenant_id == job.tenant_id,
            RrugcImageOutputVersionModel.stage == "stage1",
            RrugcImageOutputVersionModel.job_id == job.id,
        )))
        # Prior versions from earlier runs are immutable. Recover the current
        # processing attempt alongside them, never replacing existing records.
        if any(row.processing_job_id == processing.id for row in existing):
            raise RuntimeError("Current attempt already has persisted versions; manual review required")
        existing_ids = {row.remote_file_id for row in existing}
        prior_output_id = job.output_remote_file_id
        tenant_id, process_id = job.tenant_id, processing.id

    storage = build_managed_storage_provider(get_settings())
    if not isinstance(storage, GoogleDriveAssetStorage):
        raise RuntimeError("Managed Google Drive storage is unavailable")
    found = await collect_outputs(storage, tenant_id, job_id, process_id, max_files=max_files)
    new_files = [item for item in found if item["id"] not in existing_ids]
    print(
        f"stage1_job={job_id} processing_job={process_id} "
        f"verified_uploaded_images={len(found)} existing_versions={len(existing)} "
        f"new_images={len(new_files)} mode={'apply' if apply else 'dry-run'}"
    )
    if not found or not new_files:
        print("No new managed outputs; left Failed without changes")
        return 0
    if not apply:
        print("No database changes. Use --apply only after verifying expected output count.")
        return len(new_files)

    with SessionLocal() as session:
        job = session.scalar(select(RrugcKeywordImageJobModel).where(
            RrugcKeywordImageJobModel.id == job_id,
            RrugcKeywordImageJobModel.tenant_id == tenant_id,
        ).with_for_update())
        if (job is None or job.status != "failed" or job.last_error_code != "keyword_image_internal_error"
            or job.processing_job_id != process_id or job.output_remote_file_id != prior_output_id):
            raise RuntimeError("Job changed while scanning; recovery stopped")
        persisted = list(session.scalars(select(RrugcImageOutputVersionModel).where(
            RrugcImageOutputVersionModel.tenant_id == tenant_id,
            RrugcImageOutputVersionModel.stage == "stage1",
            RrugcImageOutputVersionModel.job_id == job_id,
        )))
        if {row.remote_file_id for row in persisted} != existing_ids or any(
            row.processing_job_id == process_id for row in persisted
        ):
            raise RuntimeError("Output history changed while scanning; recovery stopped")
        for index, item in enumerate(new_files):
            dimensions = item.get("imageMediaMetadata") or {}
            mime = item["mimeType"]
            save_output_version(
                session, tenant_id=tenant_id, stage="stage1", job=job,
                remote_file_id=item["id"], content_type=mime,
                size_bytes=int(item["size"]), width=dimensions.get("width"),
                height=dimensions.get("height"), processing_job_id=process_id,
                allow_multiple_per_attempt=index > 0,
                output_name=f"recovered/{index + 1:03}{EXTENSION[mime]}",
            )
        first = new_files[0]
        dimensions = first.get("imageMediaMetadata") or {}
        job.output_remote_file_id = first["id"]
        job.output_content_type = first["mimeType"]
        job.output_size_bytes = int(first["size"])
        job.output_width = dimensions.get("width")
        job.output_height = dimensions.get("height")
        job.output_web_url = first.get("webViewLink")
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.last_error_code = None
        job.last_error_message = None
        session.commit()
    print(f"Recovered {len(new_files)} existing images without rerunning Codex")
    return len(new_files)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-files", type=int, default=500)
    args = parser.parse_args()
    if not 1 <= args.max_files <= 5000:
        parser.error("--max-files must be between 1 and 5000")
    asyncio.run(recover(args.job_id, apply=args.apply, max_files=args.max_files))


if __name__ == "__main__":
    main()
