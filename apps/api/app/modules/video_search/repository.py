from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.video_search.fingerprint import build_video_analysis_idempotency_key
from app.modules.video_search.model import (
    VideoAnalysisChunkModel,
    VideoAnalysisRunModel,
    VideoMetadataProfileModel,
    utcnow,
)


class VideoStateTransitionError(ValueError):
    pass


class VideoChunkLayoutConflictError(ValueError):
    pass


class VideoRunConflictError(RuntimeError):
    pass


_RUN_TRANSITIONS = {
    "preparing": {"pending", "failed"},
    "analyzing": {"preparing"},
    "completed": {"analyzing"},
    "failed": {"pending", "preparing", "analyzing"},
    "cancelled": {"pending", "preparing", "analyzing", "failed"},
}

_CHUNK_TRANSITIONS = {
    "preparing": {"pending", "failed"},
    "uploaded": {"preparing"},
    "analyzing": {"uploaded"},
    "completed": {"analyzing"},
    "failed": {"pending", "preparing", "uploaded", "analyzing"},
}


class VideoSearchRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_profile(
        self, tenant_id: str, profile_id: str
    ) -> VideoMetadataProfileModel | None:
        return self.session.scalar(
            select(VideoMetadataProfileModel).where(
                VideoMetadataProfileModel.tenant_id == tenant_id,
                VideoMetadataProfileModel.id == profile_id,
            )
        )

    def get_active_profile(self, tenant_id: str) -> VideoMetadataProfileModel | None:
        return self.session.scalar(
            select(VideoMetadataProfileModel)
            .where(
                VideoMetadataProfileModel.tenant_id == tenant_id,
                VideoMetadataProfileModel.active.is_(True),
            )
            .order_by(
                VideoMetadataProfileModel.created_at.desc(),
                VideoMetadataProfileModel.id.desc(),
            )
            .limit(1)
        )

    def get_run(self, *, tenant_id: str, run_id: str) -> VideoAnalysisRunModel | None:
        return self.session.scalar(
            select(VideoAnalysisRunModel).where(
                VideoAnalysisRunModel.tenant_id == tenant_id,
                VideoAnalysisRunModel.id == run_id,
            )
        )

    def get_run_by_idempotency_key(
        self, *, tenant_id: str, idempotency_key: str
    ) -> VideoAnalysisRunModel | None:
        return self.session.scalar(
            select(VideoAnalysisRunModel).where(
                VideoAnalysisRunModel.tenant_id == tenant_id,
                VideoAnalysisRunModel.idempotency_key == idempotency_key,
            )
        )

    def get_or_create_run(self, **values: Any) -> VideoAnalysisRunModel:
        tenant_id = values["tenant_id"]
        idempotency_key = build_video_analysis_idempotency_key(**values)
        existing = self.get_run_by_idempotency_key(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing

        values = dict(values)
        values["idempotency_key"] = idempotency_key
        try:
            with self.session.begin_nested():
                run = VideoAnalysisRunModel(**values)
                self.session.add(run)
                self.session.flush()
            return run
        except IntegrityError:
            existing = self.get_run_by_idempotency_key(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing
            raise

    def list_chunks(
        self, *, tenant_id: str, run_id: str
    ) -> list[VideoAnalysisChunkModel]:
        return list(
            self.session.scalars(
                select(VideoAnalysisChunkModel)
                .where(
                    VideoAnalysisChunkModel.tenant_id == tenant_id,
                    VideoAnalysisChunkModel.run_id == run_id,
                )
                .order_by(VideoAnalysisChunkModel.chunk_index.asc())
            )
        )

    def create_chunks(
        self,
        *,
        tenant_id: str,
        run_id: str,
        layouts: Iterable[Mapping[str, int]],
    ) -> list[VideoAnalysisChunkModel]:
        run = self._run(tenant_id=tenant_id, run_id=run_id, lock=True)
        canonical = self._validate_layouts(layouts)
        existing = self.list_chunks(tenant_id=tenant_id, run_id=run_id)
        existing_by_index = {chunk.chunk_index: chunk for chunk in existing}

        for layout in canonical:
            chunk = existing_by_index.get(layout["chunk_index"])
            if chunk is not None and (
                chunk.source_start_ms != layout["source_start_ms"]
                or chunk.source_end_ms != layout["source_end_ms"]
            ):
                raise VideoChunkLayoutConflictError("chunk layout differs from canonical layout")

        requested_indexes = {layout["chunk_index"] for layout in canonical}
        existing_indexes = set(existing_by_index)
        if existing_indexes != requested_indexes:
            if existing_indexes:
                raise VideoChunkLayoutConflictError("chunk index set differs from canonical layout")

        if not existing:
            run.total_chunks = len(canonical)
            if run.completed_chunks > run.total_chunks:
                raise VideoChunkLayoutConflictError("completed chunk count exceeds requested layout")
            for layout in canonical:
                chunk = VideoAnalysisChunkModel(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    **layout,
                )
                self.session.add(chunk)
            self.session.flush()
            return self.list_chunks(tenant_id=tenant_id, run_id=run_id)

        if run.total_chunks != len(canonical):
            raise VideoChunkLayoutConflictError("stored total chunk count differs from layout")
        return existing

    def mark_run_preparing(self, *, tenant_id: str, run_id: str) -> VideoAnalysisRunModel:
        run = self._run(tenant_id=tenant_id, run_id=run_id, lock=True)
        self._transition_run(run, "preparing")
        run.attempt_count += 1
        if run.started_at is None:
            run.started_at = utcnow()
        run.last_error_code = None
        run.last_error_message = None
        self.session.flush()
        return run

    def mark_run_analyzing(self, *, tenant_id: str, run_id: str) -> VideoAnalysisRunModel:
        run = self._run(tenant_id=tenant_id, run_id=run_id, lock=True)
        self._transition_run(run, "analyzing")
        self.session.flush()
        return run

    def complete_run(
        self, *, tenant_id: str, run_id: str, summary_json: Mapping[str, Any] | None = None
    ) -> VideoAnalysisRunModel:
        run = self._run(tenant_id=tenant_id, run_id=run_id, lock=True)
        if run.status not in _RUN_TRANSITIONS["completed"]:
            raise VideoStateTransitionError(
                f"invalid run transition: {run.status} -> completed"
            )
        if run.total_chunks > 0 and run.completed_chunks != run.total_chunks:
            raise VideoStateTransitionError("cannot complete run with unfinished chunks")
        run.status = "completed"
        run.completed_at = utcnow()
        if summary_json is not None:
            run.summary_json = dict(summary_json)
        run.last_error_code = None
        run.last_error_message = None
        self.session.flush()
        return run

    def fail_run(
        self, *, tenant_id: str, run_id: str, error_code: str, error_message: str
    ) -> VideoAnalysisRunModel:
        run = self._run(tenant_id=tenant_id, run_id=run_id, lock=True)
        self._transition_run(run, "failed")
        run.last_error_code = error_code[:100]
        run.last_error_message = error_message
        self.session.flush()
        return run

    def cancel_run(self, *, tenant_id: str, run_id: str) -> VideoAnalysisRunModel:
        run = self._run(tenant_id=tenant_id, run_id=run_id, lock=True)
        self._transition_run(run, "cancelled")
        run.completed_at = utcnow()
        self.session.flush()
        return run

    def mark_chunk_preparing(
        self, *, tenant_id: str, run_id: str, chunk_id: str
    ) -> VideoAnalysisChunkModel:
        chunk = self._chunk(tenant_id=tenant_id, run_id=run_id, chunk_id=chunk_id, lock=True)
        self._transition_chunk(chunk, "preparing")
        chunk.attempt_count += 1
        if chunk.started_at is None:
            chunk.started_at = utcnow()
        chunk.last_error_code = None
        chunk.last_error_message = None
        self.session.flush()
        return chunk

    def mark_chunk_uploaded(
        self,
        *,
        tenant_id: str,
        run_id: str,
        chunk_id: str,
        proxy_size_bytes: int | None = None,
        provider_file_name: str | None = None,
        provider_file_uri: str | None = None,
    ) -> VideoAnalysisChunkModel:
        if proxy_size_bytes is not None and proxy_size_bytes < 0:
            raise ValueError("proxy_size_bytes must be non-negative")
        chunk = self._chunk(tenant_id=tenant_id, run_id=run_id, chunk_id=chunk_id, lock=True)
        self._transition_chunk(chunk, "uploaded")
        if proxy_size_bytes is not None:
            chunk.proxy_size_bytes = proxy_size_bytes
        if provider_file_name is not None:
            chunk.provider_file_name = provider_file_name
        if provider_file_uri is not None:
            chunk.provider_file_uri = provider_file_uri
        self.session.flush()
        return chunk

    def mark_chunk_analyzing(
        self, *, tenant_id: str, run_id: str, chunk_id: str
    ) -> VideoAnalysisChunkModel:
        chunk = self._chunk(tenant_id=tenant_id, run_id=run_id, chunk_id=chunk_id, lock=True)
        self._transition_chunk(chunk, "analyzing")
        self.session.flush()
        return chunk

    def complete_chunk(
        self,
        *,
        tenant_id: str,
        run_id: str,
        chunk_id: str,
        metadata_json: Mapping[str, Any],
        usage_json: Mapping[str, Any] | None = None,
        provider_metadata_json: Mapping[str, Any] | None = None,
    ) -> VideoAnalysisChunkModel:
        run = self._run(tenant_id=tenant_id, run_id=run_id, lock=True)
        chunk = self._chunk(tenant_id=tenant_id, run_id=run_id, chunk_id=chunk_id, lock=True)
        if chunk.status == "completed":
            return chunk
        self._transition_chunk(chunk, "completed")
        if run.completed_chunks >= run.total_chunks:
            raise VideoStateTransitionError("completed chunk count cannot exceed total chunks")
        chunk.status = "completed"
        chunk.completed_at = utcnow()
        chunk.metadata_json = dict(metadata_json)
        if usage_json is not None:
            chunk.usage_json = dict(usage_json)
        if provider_metadata_json is not None:
            chunk.provider_metadata_json = dict(provider_metadata_json)
        run.completed_chunks += 1
        self.session.flush()
        return chunk

    def fail_chunk(
        self, *, tenant_id: str, run_id: str, chunk_id: str, error_code: str, error_message: str
    ) -> VideoAnalysisChunkModel:
        chunk = self._chunk(tenant_id=tenant_id, run_id=run_id, chunk_id=chunk_id, lock=True)
        self._transition_chunk(chunk, "failed")
        chunk.last_error_code = error_code[:100]
        chunk.last_error_message = error_message
        self.session.flush()
        return chunk

    def _run(self, *, tenant_id: str, run_id: str, lock: bool) -> VideoAnalysisRunModel:
        statement = select(VideoAnalysisRunModel).where(
            VideoAnalysisRunModel.tenant_id == tenant_id,
            VideoAnalysisRunModel.id == run_id,
        )
        if lock:
            statement = statement.with_for_update()
        run = self.session.scalar(statement)
        if run is None:
            raise LookupError(run_id)
        return run

    def _chunk(
        self, *, tenant_id: str, run_id: str, chunk_id: str, lock: bool
    ) -> VideoAnalysisChunkModel:
        statement = select(VideoAnalysisChunkModel).where(
            VideoAnalysisChunkModel.tenant_id == tenant_id,
            VideoAnalysisChunkModel.run_id == run_id,
            VideoAnalysisChunkModel.id == chunk_id,
        )
        if lock:
            statement = statement.with_for_update()
        chunk = self.session.scalar(statement)
        if chunk is None:
            raise LookupError(chunk_id)
        return chunk

    @staticmethod
    def _validate_layouts(layouts: Iterable[Mapping[str, int]]) -> list[dict[str, int]]:
        canonical = []
        seen_indexes = set()
        for layout in layouts:
            try:
                index = layout["chunk_index"]
                start = layout["source_start_ms"]
                end = layout["source_end_ms"]
            except KeyError as exc:
                raise VideoChunkLayoutConflictError("chunk layout is incomplete") from exc
            if index < 0 or start < 0 or end <= start:
                raise VideoChunkLayoutConflictError("chunk layout has invalid range")
            if index in seen_indexes:
                raise VideoChunkLayoutConflictError("duplicate chunk_index in layout")
            seen_indexes.add(index)
            canonical.append({
                "chunk_index": index,
                "source_start_ms": start,
                "source_end_ms": end,
            })
        return sorted(canonical, key=lambda layout: layout["chunk_index"])

    @staticmethod
    def _transition_run(run: VideoAnalysisRunModel, target: str) -> None:
        if run.status not in _RUN_TRANSITIONS[target]:
            raise VideoStateTransitionError(f"invalid run transition: {run.status} -> {target}")
        run.status = target

    @staticmethod
    def _transition_chunk(chunk: VideoAnalysisChunkModel, target: str) -> None:
        if chunk.status not in _CHUNK_TRANSITIONS[target]:
            raise VideoStateTransitionError(f"invalid chunk transition: {chunk.status} -> {target}")
        chunk.status = target
