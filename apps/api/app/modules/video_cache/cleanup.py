"""Bounded, DB-driven R2 cleanup and explicit READY reconciliation."""
from __future__ import annotations
import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import and_, or_, select
from app.modules.processing.model import ProcessingJobModel
from app.modules.video_cache.model import VideoCacheObjectModel, utcnow
from app.modules.video_cache.quota import VideoCacheQuota, is_exact_cache_key

from app.providers.cloudflare.r2 import R2NotFound, R2ProviderError


def _due(value, now):
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= now


class VideoCacheCleanup:
    def __init__(self, session_factory, settings, provider):
        self.session_factory = session_factory
        self.settings = settings
        self.provider = provider
        self.quota = VideoCacheQuota(session_factory, settings, provider)

    def _claim_abandoned(self):
        owner = str(uuid4())
        cutoff = utcnow() - timedelta(seconds=self.settings.R2_VIDEO_CACHE_PREPARING_STALE_SECONDS)
        with self.quota.transaction() as session:
            rows = session.scalars(select(VideoCacheObjectModel).outerjoin(
                ProcessingJobModel,
                and_(ProcessingJobModel.tenant_id == VideoCacheObjectModel.tenant_id,
                     ProcessingJobModel.id == VideoCacheObjectModel.fill_job_id),
            ).where(
                VideoCacheObjectModel.status == "preparing",
                VideoCacheObjectModel.updated_at < cutoff,
                or_(ProcessingJobModel.id.is_(None),
                    ProcessingJobModel.status.in_(("completed", "failed")),
                    and_(
                        ProcessingJobModel.status == "processing",
                        ProcessingJobModel.attempt_count >= ProcessingJobModel.max_attempts,
                        ProcessingJobModel.lease_expires_at < cutoff,
                    )),
            ).order_by(VideoCacheObjectModel.updated_at, VideoCacheObjectModel.id)
             .limit(self.settings.R2_VIDEO_CACHE_CLEANUP_BATCH_SIZE)).all()
            for row in rows:
                if not is_exact_cache_key(row.tenant_id, row.content_hash, row.r2_key):
                    continue
                row.status = "deleting"
                row.size_bytes = max(row.size_bytes, row.reserved_bytes)
                row.reserved_bytes = 0
                row.cleanup_claimed_by = owner
                row.cleanup_lease_expires_at = utcnow() + timedelta(minutes=5)
                row.updated_at = utcnow()
                session.flush()
                return row.tenant_id, row.id, owner
        return None

    def _claim_due_delete(self):
        now = utcnow()
        owner = str(uuid4())
        with self.quota.transaction() as session:
            rows = session.scalars(select(VideoCacheObjectModel).where(
                VideoCacheObjectModel.status == "deleting",
                or_(VideoCacheObjectModel.next_attempt_at.is_(None), VideoCacheObjectModel.next_attempt_at <= now),
                or_(VideoCacheObjectModel.cleanup_lease_expires_at.is_(None), VideoCacheObjectModel.cleanup_lease_expires_at <= now),
            ).order_by(VideoCacheObjectModel.updated_at, VideoCacheObjectModel.id)
             .limit(self.settings.R2_VIDEO_CACHE_CLEANUP_BATCH_SIZE)).all()
            for row in rows:
                if not _due(row.next_attempt_at, now) or not _due(row.cleanup_lease_expires_at, now):
                    continue
                if not is_exact_cache_key(row.tenant_id, row.content_hash, row.r2_key):
                    continue
                row.cleanup_claimed_by = owner
                row.cleanup_lease_expires_at = now + timedelta(minutes=5)
                row.updated_at = now
                session.flush()
                return row.tenant_id, row.id, owner
        return None

    async def run_once(self):
        if not self.settings.R2_VIDEO_CACHE_ENABLED:
            return {"recovered": 0, "deleted": 0, "evicted": 0}
        recovered = deleted = evicted = 0
        maximum = min(self.settings.R2_VIDEO_CACHE_CLEANUP_BATCH_SIZE,
                      self.settings.R2_VIDEO_CACHE_CLEANUP_MAX_ITEMS_PER_RUN)
        attempted = 0
        for _ in range(maximum):
            claim = self._claim_abandoned()
            if claim is None:
                break
            attempted += 1
            recovered += 1
            deleted += int(await self.quota.delete_claimed(*claim))
        for _ in range(maximum - attempted):
            claim = self._claim_due_delete()
            if claim is None:
                break
            attempted += 1
            deleted += int(await self.quota.delete_claimed(*claim))
        for _ in range(maximum - attempted):
            with self.quota.transaction() as session:
                usage = self.quota.usage(session).effective_bytes
            if usage <= self.settings.R2_VIDEO_CACHE_SOFT_LIMIT_BYTES:
                break
            if not await self.quota.evict_one():
                break
            evicted += 1
        return {"recovered": recovered, "deleted": deleted, "evicted": evicted}

    async def reconcile_ready(self, *, limit: int | None = None):
        """Explicit bounded HEAD audit; never list or delete unknown bucket keys."""
        if limit is not None and limit <= 0:
            raise ValueError("Reconciliation limit must be positive")
        maximum = min(limit or self.settings.R2_VIDEO_CACHE_CLEANUP_BATCH_SIZE,
                      self.settings.R2_VIDEO_CACHE_CLEANUP_MAX_ITEMS_PER_RUN)
        with self.session_factory() as session:
            rows = tuple(session.execute(select(
                VideoCacheObjectModel.tenant_id, VideoCacheObjectModel.id,
                VideoCacheObjectModel.r2_key, VideoCacheObjectModel.content_hash,
                VideoCacheObjectModel.size_bytes, VideoCacheObjectModel.cached_at,
            ).where(VideoCacheObjectModel.status == "ready")
             .order_by(VideoCacheObjectModel.cached_at, VideoCacheObjectModel.id)
             .limit(maximum)).all())
        result = {"checked": 0, "missing": 0, "size_mismatch": 0}
        for tenant, record_id, key, digest, size, cached_at in rows:
            if not is_exact_cache_key(tenant, digest, key):
                continue
            try:
                head = await self.provider.head_object(key)
                mismatch = head.size_bytes != size
                missing = False
            except R2NotFound:
                mismatch = False
                missing = True
            except R2ProviderError:
                continue
            result["checked"] += 1
            result["missing"] += int(missing)
            result["size_mismatch"] += int(mismatch)
            if missing or mismatch:
                owner = str(uuid4())
                with self.quota.transaction() as session:
                    row = session.scalar(select(VideoCacheObjectModel).where(
                        VideoCacheObjectModel.tenant_id == tenant,
                        VideoCacheObjectModel.id == record_id,
                        VideoCacheObjectModel.status == "ready"))
                    if (row is None or row.r2_key != key or row.content_hash != digest
                            or row.size_bytes != size or row.cached_at != cached_at):
                        continue
                    if mismatch:
                        row.size_bytes = max(row.size_bytes, head.size_bytes)
                    row.status = "deleting"
                    row.cleanup_claimed_by = owner
                    row.cleanup_lease_expires_at = utcnow() + timedelta(minutes=5)
                    row.last_error_code = "r2_missing" if missing else "r2_size_mismatch"
                    row.last_error_message = "Video cache object requires cleanup"
                await self.quota.delete_claimed(tenant, record_id, owner)
        return result


class VideoCacheCleanupRunner:
    def __init__(self, session_factory, settings, *, logger=None):
        self.session_factory = session_factory
        self.settings = settings
        self.logger = logger or logging.getLogger("cam.worker")
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if not self.settings.R2_VIDEO_CACHE_ENABLED or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="video-cache-cleanup", daemon=True)
        self._thread.start()

    def _loop(self):
        from app.providers.cloudflare.r2 import R2Adapter
        while not self._stop.is_set():
            try:
                asyncio.run(VideoCacheCleanup(
                    self.session_factory, self.settings, R2Adapter(self.settings),
                ).run_once())
            except Exception as exc:
                self.logger.error("video_cache_cleanup_failed", extra={"error_code": type(exc).__name__})
            self._stop.wait(self.settings.R2_VIDEO_CACHE_CLEANUP_INTERVAL_SECONDS)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
