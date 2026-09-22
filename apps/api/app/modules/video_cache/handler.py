"""Durable byte-exact video-cache fill worker."""
from __future__ import annotations
import asyncio
from collections.abc import Mapping
from sqlalchemy import select
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult, JobOutcome
from app.modules.assets.content_resolver import SourceAssetContentResolver, SourceAssetContentTransient
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, SourceAssetModel
from app.modules.video_cache.model import VideoCacheObjectModel, utcnow
from app.modules.video_cache.metrics import emit_counter
from app.modules.video_cache.quota import VideoCacheQuota
from app.modules.video_cache.playback import schedule_video_playback_prepare
from app.modules.video_cache.repository import VideoCacheRepository
from app.modules.video_cache.service import VideoCacheService, VideoCacheIntegrityError, video_cache_key
from app.providers.cloudflare.r2 import R2Adapter, R2ProviderError


class VideoCacheFillJobHandler:
    def __init__(self, settings):
        self.settings = settings

    @staticmethod
    def _log_exception(
        context: JobHandlerContext,
        *,
        phase: str,
        exc: Exception,
        retryable: bool,
    ) -> None:
        """Emit safe cache-fill diagnostics without exception details."""
        try:
            # Python 3.12 LoggerAdapter replaces call-site ``extra`` with the
            # adapter context instead of merging it.  Merge explicitly so the
            # worker/job fields and the bounded diagnostic fields both reach
            # the structured formatter.
            log_fields = dict(context.logger.extra)
            log_fields.update({
                "phase": phase,
                "exception_type": type(exc).__name__,
                "retryable": retryable,
            })
            context.logger.logger.error(
                "video_cache_fill_exception",
                extra=log_fields,
            )
        except Exception:
            # Diagnostic logging must never alter job semantics.
            return

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        if not self.settings.R2_VIDEO_CACHE_ENABLED:
            return JobHandlerResult.non_retryable("cache_disabled", "Video cache is disabled")
        emit_counter("video_cache_fill_started_total")
        try:
            result = asyncio.run(self._run(context))
        except Exception as exc:
            self._log_exception(
                context,
                phase="handler",
                exc=exc,
                retryable=True,
            )
            result = JobHandlerResult.retryable("cache_internal_error", "Video cache fill failed")
        if result.outcome is JobOutcome.COMPLETED:
            emit_counter("video_cache_fill_completed_total")
        elif result.outcome is not JobOutcome.CANCELLED:
            emit_counter("video_cache_fill_failed_total")
        return result

    def _valid(self, session, job):
        p = job.payload
        if not isinstance(p, Mapping) or p.get("tenant_id") != job.tenant_id:
            return None
        tenant, asset_id, source_id, digest = (
            job.tenant_id, p.get("asset_id"), p.get("source_asset_id"), p.get("content_hash"))
        if not all(isinstance(x, str) for x in (asset_id, source_id, digest)):
            return None
        try:
            key = video_cache_key(tenant, digest)
        except ValueError:
            return None
        row = session.scalar(select(VideoCacheObjectModel).where(
            VideoCacheObjectModel.tenant_id == tenant, VideoCacheObjectModel.id == job.entity_id,
            VideoCacheObjectModel.fill_job_id == job.id, VideoCacheObjectModel.status == "preparing",
            VideoCacheObjectModel.content_hash == digest, VideoCacheObjectModel.r2_key == key))
        asset = session.scalar(select(AssetModel).where(
            AssetModel.tenant_id == tenant, AssetModel.id == asset_id))
        source = session.scalar(select(SourceAssetModel).where(
            SourceAssetModel.tenant_id == tenant, SourceAssetModel.id == source_id,
            SourceAssetModel.deleted_at.is_(None)))
        link = session.scalar(select(AssetSourceLinkModel.id).where(
            AssetSourceLinkModel.tenant_id == tenant, AssetSourceLinkModel.asset_id == asset_id,
            AssetSourceLinkModel.source_asset_id == source_id))
        if (row is None or asset is None or source is None or link is None
            or row.asset_id != asset_id or row.source_asset_id != source_id
            or asset.content_hash != digest or not (asset.mime_type or "").startswith("video/")
            or not (source.mime_type or "").startswith("video/")
            or not isinstance(asset.size_bytes, int) or asset.size_bytes <= 0
            or asset.size_bytes != source.size_bytes or row.reserved_bytes != asset.size_bytes
            or asset.size_bytes > self.settings.R2_VIDEO_CACHE_MAX_OBJECT_BYTES):
            return None
        return (key, source_id, digest, asset.size_bytes, asset.mime_type)

    def _row(self, session, job):
        return session.scalar(select(VideoCacheObjectModel).where(
            VideoCacheObjectModel.tenant_id == job.tenant_id,
            VideoCacheObjectModel.id == job.entity_id,
            VideoCacheObjectModel.fill_job_id == job.id))

    async def _cleanup(self, quota, provider, job, key, upload_id):
        try:
            if key != video_cache_key(job.tenant_id, job.payload.get("content_hash", "")):
                raise ValueError("Unsafe cache key")
            if upload_id:
                await provider.abort_multipart_upload(key, upload_id)
            await provider.delete_object(key)
            return True
        except (R2ProviderError, ValueError):
            # Remote state is uncertain: count the full reservation as DELETING.
            with quota.transaction() as session:
                row = self._row(session, job)
                if row is not None and row.status == "preparing":
                    row.size_bytes = max(row.size_bytes, row.reserved_bytes)
                    row.reserved_bytes = 0
                    row.status = "deleting"
                    row.last_error_code = "cleanup_pending"
                    row.last_error_message = "Video cache cleanup pending"
                    row.updated_at = utcnow()
            return False

    def _fail(self, quota, job, code):
        with quota.transaction() as session:
            row = self._row(session, job)
            if row is not None and row.status == "preparing":
                row.multipart_upload_id = None
                VideoCacheRepository(session).mark_failed(job.tenant_id, row.id, code=code)

    async def _run(self, context):
        job = context.job
        phase = "validate"

        def set_phase(name: str) -> None:
            nonlocal phase
            phase = name

        provider = context.dependencies.resources.get("video_cache_r2_provider") or R2Adapter(self.settings)
        quota = VideoCacheQuota(context.dependencies.session_factory, self.settings, provider)
        set_phase("validate")
        with quota.transaction() as session:
            identity = self._valid(session, job)
            row = self._row(session, job)
            if row is None or row.status != "preparing":
                return JobHandlerResult.non_retryable("cache_state_changed", "Video cache state changed")
            upload_id = row.multipart_upload_id
            key = row.r2_key
        if identity is None:
            set_phase("cleanup")
            if not await self._cleanup(quota, provider, job, key, upload_id):
                return JobHandlerResult.non_retryable("cleanup_pending", "Video cache cleanup pending")
            self._fail(quota, job, "source_changed")
            return JobHandlerResult.non_retryable("source_changed", "Video source changed")
        key, source_id, digest, size, mime = identity
        if upload_id:
            set_phase("cleanup")
            if not await self._cleanup(quota, provider, job, key, upload_id):
                return JobHandlerResult.non_retryable("cleanup_pending", "Video cache cleanup pending")
        async def remember(upload):
            with quota.transaction() as session:
                row = self._row(session, job)
                if row is None or row.status != "preparing":
                    raise VideoCacheIntegrityError("Cache state changed")
                row.multipart_upload_id = upload
                row.updated_at = utcnow()
        async def checked(body):
            set_phase("source_read")
            async for block in body:
                if context.is_cancelled or context.shutdown_requested.is_set():
                    raise VideoCacheIntegrityError("Cache fill interrupted")
                yield block
        resolver = (context.dependencies.resources.get("video_cache_content_resolver")
                    or SourceAssetContentResolver(context.dependencies.session_factory))
        try:
            set_phase("source_provider_setup")
            async with resolver.open(tenant_id=job.tenant_id, source_asset_id=source_id) as stream:
                result = await VideoCacheService(
                    provider, max_object_bytes=self.settings.R2_VIDEO_CACHE_MAX_OBJECT_BYTES
                ).upload_original(checked(stream.body), tenant_id=job.tenant_id,
                    content_hash_expected=digest, expected_size_bytes=size,
                    mime_type=mime, on_upload_started=remember, on_phase=set_phase)
            set_phase("db_finalize")
            with quota.transaction() as session:
                if self._valid(session, job) != identity:
                    raise VideoCacheIntegrityError("Source changed during fill")
                VideoCacheRepository(session).mark_ready(
                    job.tenant_id, job.entity_id, size_bytes=result.size_bytes, etag=result.etag)
            # Derived playback is a best-effort acceleration layer. Its admission
            # runs only after the immutable original is durably READY, so a job/
            # quota failure can never roll back or delete a valid original.
            try:
                with quota.transaction() as session:
                    row = VideoCacheRepository(session).get_by_id(job.tenant_id, job.entity_id)
                    if row is not None:
                        schedule_video_playback_prepare(session, self.settings, row)
            except Exception:
                pass
            return JobHandlerResult.completed()
        except Exception as exc:
            retryable = (isinstance(exc, SourceAssetContentTransient)
                         or isinstance(exc, R2ProviderError) and exc.retryable)
            self._log_exception(
                context,
                phase=phase,
                exc=exc,
                retryable=retryable,
            )
            set_phase("cleanup")
            with quota.transaction() as session:
                row = self._row(session, job)
                upload_id = row.multipart_upload_id if row is not None else None
            if not await self._cleanup(quota, provider, job, key, upload_id):
                return JobHandlerResult.non_retryable("cleanup_pending", "Video cache cleanup pending")
            if not retryable or job.attempt_count >= 5:
                self._fail(quota, job, "cache_fill_failed")
                return JobHandlerResult.non_retryable("cache_fill_failed", "Video cache fill failed")
            with quota.transaction() as session:
                row = self._row(session, job)
                if row is not None and row.status == "preparing":
                    row.multipart_upload_id = None
                    row.updated_at = utcnow()
            return JobHandlerResult.retryable("cache_fill_transient", "Video cache fill will retry")
