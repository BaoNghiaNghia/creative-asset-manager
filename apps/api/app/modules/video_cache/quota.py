"""Bucket-global serialized quota and conservative physical-byte accounting."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator
from uuid import uuid4

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.video_cache.model import VideoCacheObjectModel
from app.modules.video_cache.metrics import emit_counter
from app.modules.video_cache.service import video_cache_key
from app.providers.cloudflare.r2 import R2Adapter, R2NotFound, R2ProviderError

_SQLITE_QUOTA_LOCK = threading.RLock()
_QUOTA_LOCK_KEY = "video-cache-bucket-global"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_exact_cache_key(tenant_id: str, content_hash: str, key: str) -> bool:
    try:
        return key == video_cache_key(tenant_id, content_hash)
    except ValueError:
        return False


@dataclass(frozen=True)
class VideoCacheUsage:
    ready_bytes: int
    deleting_bytes: int
    reserved_bytes: int

    @property
    def effective_bytes(self) -> int:
        return self.ready_bytes + self.deleting_bytes + self.reserved_bytes


class VideoCacheQuota:
    def __init__(self, session_factory: Callable[[], Session], settings: Settings, provider: R2Adapter):
        self.session_factory = session_factory
        self.settings = settings
        self.provider = provider

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Hold the bucket-global lock through commit, never only through SUM()."""
        with self.session_factory() as session:
            sqlite = session.get_bind().dialect.name == "sqlite"
            lock = _SQLITE_QUOTA_LOCK if sqlite else None
            if lock is not None:
                lock.acquire()
            try:
                if session.get_bind().dialect.name == "postgresql":
                    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": _QUOTA_LOCK_KEY})
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise
            finally:
                if lock is not None:
                    lock.release()

    @staticmethod
    def usage(session: Session) -> VideoCacheUsage:
        totals = dict(session.execute(
            select(VideoCacheObjectModel.status, func.coalesce(func.sum(VideoCacheObjectModel.size_bytes), 0))
            .where(VideoCacheObjectModel.status.in_(("ready", "deleting")))
            .group_by(VideoCacheObjectModel.status)
        ).all())
        reserved = session.scalar(select(func.coalesce(func.sum(VideoCacheObjectModel.reserved_bytes), 0)).where(
            VideoCacheObjectModel.status == "preparing"
        ))
        return VideoCacheUsage(int(totals.get("ready", 0)), int(totals.get("deleting", 0)), int(reserved or 0))

    def can_reserve(self, session: Session, required: int) -> bool:
        return required > 0 and self.usage(session).effective_bytes + required <= self.settings.R2_VIDEO_CACHE_HARD_LIMIT_BYTES

    def claim_lru(self, session: Session, *, owner: str | None = None) -> VideoCacheObjectModel | None:
        statement = select(VideoCacheObjectModel).where(VideoCacheObjectModel.status == "ready").order_by(
            case((VideoCacheObjectModel.last_accessed_at.is_(None), 0), else_=1),
            VideoCacheObjectModel.last_accessed_at,
            VideoCacheObjectModel.cached_at,
            VideoCacheObjectModel.id,
        ).limit(1)
        if session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        row = session.scalar(statement)
        if row is None:
            return None
        row.status = "deleting"
        row.cleanup_claimed_by = owner or str(uuid4())
        row.cleanup_lease_expires_at = utcnow() + timedelta(minutes=5)
        row.next_attempt_at = None
        row.updated_at = utcnow()
        session.flush()
        return row

    async def delete_claimed(self, tenant_id: str, record_id: str, owner: str) -> bool:
        with self.session_factory() as session:
            row = session.scalar(select(VideoCacheObjectModel).where(
                VideoCacheObjectModel.tenant_id == tenant_id,
                VideoCacheObjectModel.id == record_id,
                VideoCacheObjectModel.status == "deleting",
                VideoCacheObjectModel.cleanup_claimed_by == owner,
            ))
            if row is None or not is_exact_cache_key(row.tenant_id, row.content_hash, row.r2_key):
                return False
            key = row.r2_key
            upload_id = row.multipart_upload_id
        try:
            if upload_id:
                try:
                    await self.provider.abort_multipart_upload(key, upload_id)
                except R2NotFound:
                    pass
            try:
                await self.provider.delete_object(key)
            except R2NotFound:
                pass
        except R2ProviderError:
            with self.transaction() as session:
                row = session.scalar(select(VideoCacheObjectModel).where(
                    VideoCacheObjectModel.tenant_id == tenant_id,
                    VideoCacheObjectModel.id == record_id,
                    VideoCacheObjectModel.status == "deleting",
                    VideoCacheObjectModel.cleanup_claimed_by == owner,
                ))
                if row is not None:
                    row.cleanup_claimed_by = None
                    row.cleanup_lease_expires_at = None
                    row.next_attempt_at = utcnow() + timedelta(minutes=5)
                    row.attempt_count += 1
                    row.last_error_code = "r2_delete_retry"
                    row.last_error_message = "Video cache deletion will retry"
                    row.updated_at = utcnow()
            return False
        with self.transaction() as session:
            row = session.scalar(select(VideoCacheObjectModel).where(
                VideoCacheObjectModel.tenant_id == tenant_id,
                VideoCacheObjectModel.id == record_id,
                VideoCacheObjectModel.status == "deleting",
                VideoCacheObjectModel.cleanup_claimed_by == owner,
            ))
            if row is not None:
                session.delete(row)
        return True

    async def evict_one(self) -> bool:
        owner = str(uuid4())
        with self.transaction() as session:
            row = self.claim_lru(session, owner=owner)
            identity = (row.tenant_id, row.id) if row is not None else None
        if identity is None:
            return False
        deleted = await self.delete_claimed(*identity, owner)
        if deleted:
            emit_counter("video_cache_eviction_total")
        return deleted

    async def reclaim_for(self, required: int) -> bool:
        """On pressure, target SOFT including incoming bytes; never count DELETING as free."""
        for _ in range(self.settings.R2_VIDEO_CACHE_CLEANUP_MAX_ITEMS_PER_RUN):
            with self.transaction() as session:
                usage = self.usage(session)
                if usage.deleting_bytes + usage.reserved_bytes + required > self.settings.R2_VIDEO_CACHE_SOFT_LIMIT_BYTES:
                    return False
                if usage.effective_bytes + required <= self.settings.R2_VIDEO_CACHE_SOFT_LIMIT_BYTES:
                    return True
                owner = str(uuid4())
                row = self.claim_lru(session, owner=owner)
                identity = (row.tenant_id, row.id) if row is not None else None
            if identity is None:
                return False
            await self.delete_claimed(*identity, owner)
        with self.transaction() as session:
            return self.usage(session).effective_bytes + required <= self.settings.R2_VIDEO_CACHE_SOFT_LIMIT_BYTES
