from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.providers.contracts import (
    AssetStorageProvider,
    OpenStoredAssetInput,
    StorageProviderError,
)
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.assets.model import AssetSourceLinkModel, SourceAssetModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.storage.model import AssetStorageObjectModel
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider

_ERROR_CODE = "analysis_storage_read_failed"
_STORAGE_PROVIDER = "google_drive_managed"




def diagnose_analysis_storage_read_failures(
    *,
    tenant_id: str,
    session_factory: Callable[[], Session],
    limit: int = 20,
    verify_remote: bool = False,
    storage_provider: AssetStorageProvider | None = None,
) -> dict[str, Any]:
    """Inspect a bounded, tenant-scoped sample without modifying database state."""
    if not tenant_id.strip():
        raise ValueError("--tenant-id is required")
    if limit < 1 or limit > 20:
        raise ValueError("--limit must be between 1 and 20")

    with session_factory() as session:
        jobs = list(
            session.scalars(
                select(ProcessingJobModel)
                .where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.job_type == "asset_analyze",
                    ProcessingJobModel.last_error_code == _ERROR_CODE,
                )
                .order_by(
                    ProcessingJobModel.updated_at.desc(), ProcessingJobModel.id.desc()
                )
                .limit(limit)
            )
        )
        rows = [_diagnostic_row(session, tenant_id, job) for job in jobs]
        # This command is deliberately read-only even if a caller changes a loaded ORM row.
        session.rollback()

    if not verify_remote:
        for row in rows:
            row.pop("_tenant_id", None)
            row.pop("_remote_file_id", None)
            row.pop("_content_type", None)
            row.pop("_size_bytes", None)

    if verify_remote:
        provider = storage_provider or UnconfiguredAssetStorageProvider()
        categories = asyncio.run(_verify_rows(provider, rows))
        for row, category in zip(rows, categories, strict=True):
            row["remote_verification_category"] = category

    return {
        "tenant_id": tenant_id,
        "read_only": True,
        "verify_remote": verify_remote,
        "limit": limit,
        "count": len(rows),
        "jobs": rows,
    }


def _diagnostic_row(
    session: Session, tenant_id: str, job: ProcessingJobModel
) -> dict[str, Any]:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    analysis_id = payload.get("analysis_id")
    if not isinstance(analysis_id, str) or not analysis_id.strip():
        analysis_id = job.entity_id if job.entity_type in {"analysis", "asset_ai_analysis"} else None

    analysis = None
    if analysis_id:
        analysis = session.scalar(
            select(AssetAiAnalysisModel).where(
                AssetAiAnalysisModel.tenant_id == tenant_id,
                AssetAiAnalysisModel.id == analysis_id,
            )
        )
    asset_id = analysis.asset_id if analysis is not None else None
    storage = None
    sources: list[SourceAssetModel] = []
    if asset_id:
        storage = session.scalar(
            select(AssetStorageObjectModel).where(
                AssetStorageObjectModel.tenant_id == tenant_id,
                AssetStorageObjectModel.asset_id == asset_id,
                AssetStorageObjectModel.storage_provider == _STORAGE_PROVIDER,
            )
        )
        sources = list(
            session.scalars(
                select(SourceAssetModel)
                .join(
                    AssetSourceLinkModel,
                    (AssetSourceLinkModel.tenant_id == SourceAssetModel.tenant_id)
                    & (AssetSourceLinkModel.source_asset_id == SourceAssetModel.id),
                )
                .where(
                    AssetSourceLinkModel.tenant_id == tenant_id,
                    AssetSourceLinkModel.asset_id == asset_id,
                )
                .order_by(SourceAssetModel.created_at.desc())
            )
        )
    active_source = next((source for source in sources if source.deleted_at is None), None)
    display_source = active_source or (sources[0] if sources else None)
    if active_source is not None:
        source_availability = "available"
    elif sources:
        source_availability = "deleted"
    else:
        source_availability = "unlinked"

    remote_id_present = bool(storage and storage.remote_file_id)
    return {
        "processing_job_id": job.id,
        "analysis_id": analysis.id if analysis is not None else analysis_id,
        "asset_id": asset_id,
        "asset_filename": display_source.filename if display_source else None,
        "analysis_status": analysis.status if analysis is not None else None,
        "analysis_failure_retryable": (
            analysis.failure_retryable if analysis is not None else None
        ),
        "storage_record_status": storage.status if storage is not None else None,
        "storage_provider": storage.storage_provider if storage is not None else None,
        "remote_file_id_present": remote_id_present,
        "remote_verification_category": "missing" if not remote_id_present else "unknown",
        "source_asset_availability": source_availability,
        "job_attempt_count": job.attempt_count,
        "job_max_attempts": job.max_attempts,
        # Internal-only input removed before returning from remote verification.
        "_tenant_id": tenant_id,
        "_remote_file_id": storage.remote_file_id if remote_id_present else None,
        "_content_type": None,
        "_size_bytes": None,
    }


async def _verify_rows(
    provider: AssetStorageProvider, rows: list[dict[str, Any]]
) -> list[str]:
    categories: list[str] = []
    for row in rows:
        remote_file_id = row.pop("_remote_file_id", None)
        tenant_id = row.pop("_tenant_id", None)
        content_type = row.pop("_content_type", None)
        size_bytes = row.pop("_size_bytes", None)
        if not remote_file_id or not row.get("asset_id"):
            categories.append("missing")
            continue
        if isinstance(provider, UnconfiguredAssetStorageProvider):
            categories.append("unconfigured")
            continue
        try:
            stream = await provider.open_asset(
                OpenStoredAssetInput(
                    tenant_id=tenant_id or "",
                    asset_id=row["asset_id"],
                    remote_file_id=remote_file_id,
                    content_type=content_type,
                    size_bytes=size_bytes,
                )
            )
            await stream.close()
            categories.append("ok")
        except StorageProviderError as exc:
            categories.append(_storage_error_category(exc))
        except TimeoutError:
            categories.append("timeout")
        except Exception:
            categories.append("unknown")
    for row in rows:
        row.pop("_remote_file_id", None)
        row.pop("_tenant_id", None)
        row.pop("_content_type", None)
        row.pop("_size_bytes", None)
    return categories


def _storage_error_category(error: StorageProviderError) -> str:
    code = getattr(error, "code", "")
    status = getattr(error, "status_code", None)
    if code == "managed_storage_object_missing" or status == 404:
        return "missing"
    if code == "managed_storage_forbidden" or status == 403:
        return "forbidden"
    if code == "managed_storage_unauthorized" or status == 401:
        return "unauthorized"
    if status == 429:
        return "rate_limited"
    if status == 408 or "timeout" in str(error).lower():
        return "timeout"
    if status is not None and status >= 500:
        return "upstream_error"
    if getattr(error, "retryable", False):
        return "upstream_error"
    if "not configured" in str(error).lower():
        return "unconfigured"
    return "unknown"


def _configured_storage_provider() -> AssetStorageProvider:
    from app.core.config import get_settings
    from app.providers.google.storage import GoogleDriveAssetStorage

    settings = get_settings()
    if not settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID or not (
        settings.GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN
        or settings.GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN
    ):
        return UnconfiguredAssetStorageProvider()
    return GoogleDriveAssetStorage(
        settings.GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN,
        root_folder_id=settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID,
        refresh_token=settings.GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect tenant-scoped analysis_storage_read_failed jobs."
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--verify-remote",
        action="store_true",
        help="Perform bounded managed-storage existence checks.",
    )
    args = parser.parse_args(argv)

    from app.core.database import SessionLocal

    result = diagnose_analysis_storage_read_failures(
        tenant_id=args.tenant_id,
        session_factory=SessionLocal,
        limit=args.limit,
        verify_remote=args.verify_remote,
        storage_provider=(
            _configured_storage_provider() if args.verify_remote else None
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
