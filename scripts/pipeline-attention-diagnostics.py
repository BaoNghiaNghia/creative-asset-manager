from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import asyncio
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.storage.managed_oauth import (
    check_managed_storage_refresh_token,
    managed_storage_oauth_status,
    resolve_managed_storage_credential,
)
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.ai_operations.pipeline import PipelineOperationsRepository
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.explorer.tenant_source import TenantSourceResolver
from app.modules.pipeline.attention_recovery import ANALYSIS_RECOVERY_CODES
from app.providers.microsoft.onedrive import close_thumbnail_stream, open_thumbnail_stream
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.storage.model import AssetStorageObjectModel

TARGET_CODES = {
    "managed_asset_storage_failed",
    "managed_storage_forbidden",
    "managed_storage_unauthorized",
    "managed_asset_storage_missing",
    "gemini_invalid_json",
    "gemini_invalid_document",
    "gemini_empty_response",
    "InvalidPipelineContent",
    "ValueError",
    "onedrive_notAllowed",
}

FAILURE_STATES = {
    "download_failed",
    "storage_failed",
    "analysis_failed",
    "projection_failed",
    "search_failed",
}


def main() -> None:
    with SessionLocal() as session:
        pipelines = list(
            session.scalars(
                select(AssetPipelineModel)
                .where(AssetPipelineModel.state.in_(FAILURE_STATES))
                .order_by(AssetPipelineModel.updated_at.desc())
            )
        )
        tenant_ids = sorted({row.tenant_id for row in pipelines})
        print(f"PIPELINE_FAILURES total={len(pipelines)} tenants={len(tenant_ids)}")

        settings = get_settings()
        oauth_status = managed_storage_oauth_status(settings)
        credential = resolve_managed_storage_credential(settings)
        print(
            "PIPELINE_FLAGS "
            f"managed_storage={settings.MANAGED_ASSET_STORAGE_ENABLED} "
            f"source_fallback={settings.AI_ANALYSIS_SOURCE_FALLBACK_ENABLED} "
            f"auto_analyze={settings.AI_AUTO_ANALYZE_ENABLED}"
        )
        print(
            "MANAGED_STORAGE_STATUS "
            f"connected={oauth_status.get('connected')} "
            f"reconnect_required={oauth_status.get('reconnect_required')} "
            f"root_folder_configured={oauth_status.get('root_folder_configured')}"
        )
        if tenant_ids and credential.refresh_token:
            try:
                checked = asyncio.run(
                    check_managed_storage_refresh_token(
                        settings,
                        credential.refresh_token,
                        tenant_id=tenant_ids[0],
                        initiating_user_id="pipeline-diagnostics",
                        save=False,
                    )
                )
                print(
                    "MANAGED_STORAGE_CHECK ok=true "
                    f"account={checked.account_email or 'masked'}"
                )
            except Exception as exc:
                print(
                    "MANAGED_STORAGE_CHECK ok=false "
                    f"error_type={type(exc).__name__} message={str(exc)[:180]}"
                )

        for tenant_id in tenant_ids:
            tenant_rows = [row for row in pipelines if row.tenant_id == tenant_id]
            source_ids = {row.source_asset_id for row in tenant_rows if row.source_asset_id}
            source_rows = {
                row.id: row
                for row in session.scalars(
                    select(SourceAssetModel).where(
                        SourceAssetModel.tenant_id == tenant_id,
                        SourceAssetModel.id.in_(source_ids or {"__none__"}),
                    )
                )
            }
            external_ids = {row.external_source_id for row in source_rows.values()}
            external_rows = {
                row.id: row
                for row in session.scalars(
                    select(ExternalSourceModel).where(
                        ExternalSourceModel.tenant_id == tenant_id,
                        ExternalSourceModel.id.in_(external_ids or {"__none__"}),
                    )
                )
            }
            asset_ids = {row.asset_id for row in tenant_rows if row.asset_id}
            storage_rows = {
                (row.asset_id, row.storage_provider): row
                for row in session.scalars(
                    select(AssetStorageObjectModel).where(
                        AssetStorageObjectModel.tenant_id == tenant_id,
                        AssetStorageObjectModel.asset_id.in_(asset_ids or {"__none__"}),
                    )
                )
            }

            code_counts = Counter()
            source_counts: dict[str, Counter[str]] = defaultdict(Counter)
            storage_counts: dict[str, Counter[str]] = defaultdict(Counter)
            messages: dict[str, Counter[str]] = defaultdict(Counter)
            content_types: dict[str, Counter[str]] = defaultdict(Counter)

            for pipeline in tenant_rows:
                code = pipeline.last_error_code or "no_pipeline_error"
                code_counts[code] += 1
                source = source_rows.get(pipeline.source_asset_id or "")
                external = external_rows.get(source.external_source_id) if source else None
                source_type = external.source_type if external else "missing_source"
                source_counts[code][source_type] += 1
                if source is not None:
                    suffix = Path(source.filename or "").suffix.lower() or "<none>"
                    mime = str(source.mime_type or "<none>").lower()
                    content_types[code][f"{suffix}|{mime}"] += 1

                storage = storage_rows.get((pipeline.asset_id or "", "google_drive_managed"))
                storage_key = (
                    f"{storage.status}:{storage.last_error_code or 'none'}"
                    if storage is not None
                    else "no_storage_row"
                )
                storage_counts[code][storage_key] += 1

                message = (pipeline.last_error_message or "").strip().replace("\n", " ")
                if message:
                    messages[code][message[:180]] += 1

            skip_counts = Counter()
            actionable = 0
            for pipeline in tenant_rows:
                category = PipelineOperationsRepository._skip_category(
                    pipeline.last_error_code,
                    pipeline.last_error_message,
                )
                if category is None:
                    actionable += 1
                else:
                    skip_counts[category] += 1
            actionable_codes = Counter(
                (pipeline.last_error_code or "no_pipeline_error")
                for pipeline in tenant_rows
                if PipelineOperationsRepository._skip_category(
                    pipeline.last_error_code,
                    pipeline.last_error_message,
                )
                is None
            )
            print(
                f"TENANT failures={len(tenant_rows)} actionable={actionable} "
                f"skipped={sum(skip_counts.values())} "
                + " ".join(f"skipped_{key}={value}" for key, value in sorted(skip_counts.items()))
            )
            print(
                "ACTIONABLE_CODES "
                + ",".join(
                    f"{key}:{value}"
                    for key, value in actionable_codes.most_common()
                )
            )
            actionable_messages = Counter(
                (pipeline.last_error_message or "").strip().replace("\n", " ")[:180]
                for pipeline in tenant_rows
                if PipelineOperationsRepository._skip_category(
                    pipeline.last_error_code,
                    pipeline.last_error_message,
                )
                is None
                and pipeline.last_error_code == "InvalidPipelineContent"
            )
            if actionable_messages:
                print(
                    "ACTIONABLE_INVALID_MESSAGES "
                    + " | ".join(
                        f"{count}x {message or '<empty>'}"
                        for message, count in actionable_messages.most_common()
                    )
                )
            for code, count in code_counts.most_common():
                if code not in TARGET_CODES and not code.startswith("managed_") and not code.startswith("gemini_"):
                    continue
                print(f"CODE {code} count={count}")
                print("  sources=" + ",".join(f"{k}:{v}" for k, v in source_counts[code].most_common()))
                print("  storage=" + ",".join(f"{k}:{v}" for k, v in storage_counts[code].most_common()))
                print("  content=" + ",".join(f"{k}:{v}" for k, v in content_types[code].most_common(12)))
                for message, message_count in messages[code].most_common(3):
                    print(f"  message[{message_count}]={message}")

            heic_failures = [
                row for row in tenant_rows
                if row.last_error_code == "InvalidPipelineContent"
                and (source_rows.get(row.source_asset_id or "") is not None)
                and str(source_rows[row.source_asset_id].mime_type or "").lower() in {"image/heic", "image/heif"}
            ][:1]
            if heic_failures:
                from app.modules.assets.content_resolver import SourceAssetContentResolver

                async def sample_heic_signatures():
                    resolver = SourceAssetContentResolver(SessionLocal)
                    samples = []
                    for pipeline in heic_failures:
                        try:
                            async with resolver.open(
                                tenant_id=tenant_id,
                                source_asset_id=pipeline.source_asset_id,
                                range_header="bytes=0-63",
                            ) as stream:
                                prefix = bytearray()
                                async for block in stream.body:
                                    prefix.extend(block)
                                    if len(prefix) >= 64:
                                        break
                                data = bytes(prefix[:64])
                                kind = "jpeg" if data.startswith(b"\\xff\\xd8\\xff") else (
                                    "png" if data.startswith(b"\\x89PNG\\r\\n\\x1a\\n") else (
                                        "bmff" if len(data) >= 12 and data[4:8] == b"ftyp" else (
                                            "html" if data.lstrip().lower().startswith((b"<html", b"<!doctype")) else "other"
                                        )
                                    )
                                )
                                brand = data[8:12].decode("ascii", "replace") if kind == "bmff" else ""
                                samples.append((kind, brand, stream.content_type))
                        except Exception as exc:
                            samples.append((f"error:{type(exc).__name__}", "", ""))
                    return samples

                signatures = asyncio.run(sample_heic_signatures())
                print("HEIC_SIGNATURE_SAMPLE " + ",".join(
                    f"{kind}:{brand or '-'}:{ctype or '-'}" for kind, brand, ctype in signatures
                ))

            not_allowed_all = [
                row
                for row in tenant_rows
                if row.last_error_code == "onedrive_notAllowed"
                and source_rows.get(row.source_asset_id or "") is not None
            ]
            if not_allowed_all:
                print(
                    "ONEDRIVE_NOT_ALLOWED_IDENTITY "
                    f"total={len(not_allowed_all)} "
                    f"asset_id_present={sum(1 for row in not_allowed_all if row.asset_id)} "
                    f"content_hash_present={sum(1 for row in not_allowed_all if row.content_hash)}"
                )
            not_allowed = not_allowed_all[:1]
            if not_allowed:
                async def sample_not_allowed_thumbnails():
                    samples = []
                    for pipeline in not_allowed:
                        source_asset = source_rows[pipeline.source_asset_id]
                        external = external_rows.get(source_asset.external_source_id)
                        if external is None:
                            samples.append(("missing_source", "", ""))
                            continue
                        try:
                            with SessionLocal() as resolver_session:
                                resolved = await TenantSourceResolver(resolver_session).resolve(
                                    tenant_id=tenant_id,
                                    external_source_id=external.id,
                                )
                            client, response = await open_thumbnail_stream(
                                resolved.access_token,
                                source_asset.external_asset_id,
                                size="large",
                            )
                            try:
                                prefix = bytearray()
                                async for chunk in response.aiter_raw():
                                    prefix.extend(chunk)
                                    if len(prefix) >= 64:
                                        break
                                data = bytes(prefix[:64])
                                kind = (
                                    "jpeg"
                                    if data.startswith(b"\xff\xd8\xff")
                                    else (
                                        "png"
                                        if data.startswith(b"\x89PNG\r\n\x1a\n")
                                        else "other"
                                    )
                                )
                                samples.append(
                                    (
                                        kind,
                                        str(response.status_code),
                                        response.headers.get("content-type", ""),
                                    )
                                )
                            finally:
                                await close_thumbnail_stream(client, response)
                        except Exception as exc:
                            samples.append((f"error:{type(exc).__name__}", "", ""))
                    return samples

                thumbnail_samples = asyncio.run(sample_not_allowed_thumbnails())
                print(
                    "ONEDRIVE_NOT_ALLOWED_THUMBNAILS "
                    + ",".join(
                        f"{kind}:{status or '-'}:{ctype or '-'}"
                        for kind, status, ctype in thumbnail_samples
                    )
                )

            projection_rows = [
                row
                for row in tenant_rows
                if row.state == "projection_failed"
                and row.last_error_code == "ValueError"
            ]
            if projection_rows:
                projection_analysis_ids = {
                    row.analysis_id for row in projection_rows if row.analysis_id
                }
                projection_analyses = {
                    analysis.id: analysis
                    for analysis in session.scalars(
                        select(AssetAiAnalysisModel).where(
                            AssetAiAnalysisModel.tenant_id == tenant_id,
                            AssetAiAnalysisModel.id.in_(
                                projection_analysis_ids or {"__none__"}
                            ),
                        )
                    )
                }
                projection_statuses = Counter()
                for row in projection_rows:
                    analysis = (
                        projection_analyses.get(row.analysis_id)
                        if row.analysis_id
                        else None
                    )
                    projection_statuses[
                        analysis.status if analysis is not None else "missing_analysis"
                    ] += 1
                print(
                    f"PROJECTION_REMAINDER total={len(projection_rows)} "
                    + "analysis_statuses="
                    + ",".join(
                        f"{key}:{value}"
                        for key, value in projection_statuses.most_common()
                    )
                )

            latest_job = (
                select(
                    ProcessingJobModel.entity_type,
                    ProcessingJobModel.entity_id,
                    ProcessingJobModel.job_type,
                    ProcessingJobModel.status,
                    ProcessingJobModel.last_error_code,
                    ProcessingJobModel.last_error_message,
                    func.row_number().over(
                        partition_by=(
                            ProcessingJobModel.entity_type,
                            ProcessingJobModel.entity_id,
                            ProcessingJobModel.job_type,
                        ),
                        order_by=(
                            ProcessingJobModel.created_at.desc(),
                            ProcessingJobModel.updated_at.desc(),
                            ProcessingJobModel.id.desc(),
                        ),
                    ).label("rn"),
                )
                .where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.last_error_code.is_not(None),
                )
                .subquery()
            )
            job_counts = Counter()
            for row in session.execute(select(latest_job).where(latest_job.c.rn == 1)):
                if row.last_error_code in TARGET_CODES:
                    job_counts[(row.job_type, row.status, row.last_error_code)] += 1
            for (job_type, status, code), count in job_counts.most_common():
                print(f"JOB {job_type} status={status} code={code} count={count}")

            failed_analysis_jobs = list(session.scalars(
                select(ProcessingJobModel).where(
                    ProcessingJobModel.tenant_id == tenant_id,
                    ProcessingJobModel.job_type == "asset_analyze",
                    ProcessingJobModel.status == "failed",
                    ProcessingJobModel.last_error_code.in_(ANALYSIS_RECOVERY_CODES),
                )
            ))
            state_counts = Counter()
            reference_counts = Counter()
            pipeline_ids = {job.entity_id for job in failed_analysis_jobs}
            pipeline_map = {
                pipeline.id: pipeline
                for pipeline in session.scalars(
                    select(AssetPipelineModel).where(
                        AssetPipelineModel.tenant_id == tenant_id,
                        AssetPipelineModel.id.in_(pipeline_ids or {"__none__"}),
                    )
                )
            }
            analysis_ids = set()
            for job in failed_analysis_jobs:
                pipeline = pipeline_map.get(job.entity_id)
                state_counts[pipeline.state if pipeline is not None else "missing_pipeline"] += 1
                if pipeline is not None and pipeline.analysis_id:
                    analysis_ids.add(pipeline.analysis_id)
                payload_analysis_id = str((job.payload_json or {}).get("analysis_id") or "").strip()
                if payload_analysis_id:
                    analysis_ids.add(payload_analysis_id)
            analysis_map = {
                analysis.id: analysis
                for analysis in session.scalars(
                    select(AssetAiAnalysisModel).where(
                        AssetAiAnalysisModel.tenant_id == tenant_id,
                        AssetAiAnalysisModel.id.in_(analysis_ids or {"__none__"}),
                    )
                )
            }
            for job in failed_analysis_jobs:
                pipeline = pipeline_map.get(job.entity_id)
                pipeline_analysis = (
                    analysis_map.get(pipeline.analysis_id)
                    if pipeline is not None and pipeline.analysis_id
                    else None
                )
                payload_analysis_id = str((job.payload_json or {}).get("analysis_id") or "").strip()
                payload_analysis = analysis_map.get(payload_analysis_id) if payload_analysis_id else None
                if pipeline_analysis is not None:
                    reference_counts["pipeline_analysis_exists"] += 1
                elif payload_analysis is not None:
                    reference_counts["payload_analysis_exists"] += 1
                elif payload_analysis_id:
                    reference_counts["payload_analysis_missing"] += 1
                else:
                    reference_counts["no_analysis_reference"] += 1
            active_profiles = session.scalar(
                select(func.count(MetadataProfileModel.id)).where(
                    MetadataProfileModel.tenant_id == tenant_id,
                    MetadataProfileModel.active.is_(True),
                )
            ) or 0
            print(
                "FAILED_ANALYSIS_REMAINDER "
                f"total={len(failed_analysis_jobs)} active_profiles={active_profiles} "
                "states=" + ",".join(f"{key}:{value}" for key, value in state_counts.most_common())
                + " refs=" + ",".join(f"{key}:{value}" for key, value in reference_counts.most_common())
            )


if __name__ == "__main__":
    main()
