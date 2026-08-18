import os
import threading
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.ai_governance.gemini_quota import GeminiProjectQuotaRepository
from app.modules.ai_governance.model import GeminiProjectQuotaStateModel
from app.modules.video_search.model import (
    VideoAnalysisChunkModel,
    VideoAnalysisRunModel,
    VideoMetadataProfileModel,
)
from app.modules.video_search.repository import VideoRunConflictError, VideoSearchRepository


DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", "")
POSTGRES_AVAILABLE = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://"))


@unittest.skipUnless(POSTGRES_AVAILABLE, "PostgreSQL integration database is not configured")
class VideoSearchPostgreSqlTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        self.marker = uuid4().hex
        self.tenant_id = f"video-pg-{self.marker}"

    def tearDown(self):
        with Session(self.engine) as session:
            session.execute(delete(VideoAnalysisChunkModel).where(
                VideoAnalysisChunkModel.tenant_id.like(self.tenant_id + "%")
            ))
            session.execute(delete(VideoAnalysisRunModel).where(
                VideoAnalysisRunModel.tenant_id.like(self.tenant_id + "%")
            ))
            session.execute(delete(VideoMetadataProfileModel).where(
                VideoMetadataProfileModel.tenant_id.like(self.tenant_id + "%")
            ))
            session.execute(delete(SourceAssetModel).where(SourceAssetModel.tenant_id.like(self.tenant_id + "%")))
            session.execute(delete(ExternalSourceModel).where(ExternalSourceModel.tenant_id.like(self.tenant_id + "%")))
            session.execute(delete(GeminiProjectQuotaStateModel).where(
                GeminiProjectQuotaStateModel.quota_scope.like(self.tenant_id + "%")
            ))
            session.commit()
        self.engine.dispose()

    def test_repository_persists_idempotent_video_run_and_exactly_once_chunk(self):
        with Session(self.engine, expire_on_commit=False) as session:
            assets = AssetRegistryRepository(session)
            source = assets.upsert_external_source(
                tenant_id=self.tenant_id,
                source_key=f"drive-{self.marker}",
                source_type="google_drive",
            )
            source_asset = assets.upsert_source_asset(
                tenant_id=self.tenant_id,
                external_source_id=source.id,
                external_asset_id=f"clip-{self.marker}",
                mime_type="video/mp4",
            )
            profile = VideoMetadataProfileModel(
                tenant_id=self.tenant_id,
                profile_name="video",
                profile_version="v1",
                prompt_template="describe",
            )
            session.add(profile)
            session.flush()
            repository = VideoSearchRepository(session)
            values = {
                "tenant_id": self.tenant_id,
                "source_asset_id": source_asset.id,
                "source_fingerprint": "a" * 64,
                "video_metadata_profile_id": profile.id,
                "metadata_profile": profile.profile_name,
                "metadata_profile_version": profile.profile_version,
                "prompt_version": "p1",
                "analysis_version": "a1",
                "ai_provider": "gemini",
                "ai_model": "flash",
                "chunk_seconds": 30,
            }
            run = repository.get_or_create_run(**values)
            self.assertEqual(repository.get_or_create_run(**values).id, run.id)
            chunks = repository.create_chunks(
                tenant_id=self.tenant_id,
                run_id=run.id,
                layouts=[{"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 1000}],
            )
            repository.mark_run_preparing(tenant_id=self.tenant_id, run_id=run.id)
            repository.mark_run_analyzing(tenant_id=self.tenant_id, run_id=run.id)
            repository.mark_chunk_preparing(tenant_id=self.tenant_id, run_id=run.id, chunk_id=chunks[0].id)
            repository.mark_chunk_uploaded(tenant_id=self.tenant_id, run_id=run.id, chunk_id=chunks[0].id)
            repository.mark_chunk_analyzing(tenant_id=self.tenant_id, run_id=run.id, chunk_id=chunks[0].id)
            repository.complete_chunk(
                tenant_id=self.tenant_id, run_id=run.id, chunk_id=chunks[0].id,
                metadata_json={"stable": True},
            )
            repository.complete_chunk(
                tenant_id=self.tenant_id, run_id=run.id, chunk_id=chunks[0].id,
                metadata_json={"stable": False},
            )
            self.assertEqual(run.completed_chunks, 1)
            self.assertEqual(chunks[0].metadata_json, {"stable": True})
            self.assertEqual(repository.complete_run(
                tenant_id=self.tenant_id, run_id=run.id
            ).status, "completed")
            session.commit()


    def _prerequisites(self, session, tenant_id):
        assets = AssetRegistryRepository(session)
        source = assets.upsert_external_source(
            tenant_id=tenant_id, source_key=f"source-{tenant_id}", source_type="google_drive"
        )
        asset = assets.upsert_source_asset(
            tenant_id=tenant_id, external_source_id=source.id,
            external_asset_id=f"asset-{tenant_id}", mime_type="video/mp4"
        )
        profile = VideoMetadataProfileModel(
            tenant_id=tenant_id, profile_name="profile", profile_version="v1",
            prompt_template="describe"
        )
        session.add(profile)
        session.flush()
        return asset, profile

    def _run_values(self, tenant_id, source_asset_id, profile_id, **overrides):
        values = {
            "tenant_id": tenant_id, "source_asset_id": source_asset_id,
            "source_fingerprint": "f" * 64, "video_metadata_profile_id": profile_id,
            "metadata_profile": "profile", "metadata_profile_version": "v1",
            "prompt_version": "p1", "analysis_version": "a1",
            "ai_provider": "gemini", "ai_model": "flash",
            "idempotency_key": "k" * 64, "status": "pending",
            "chunk_seconds": 30, "total_chunks": 0, "completed_chunks": 0,
            "attempt_count": 0,
        }
        values.update(overrides)
        return values


    @staticmethod
    def _identity(run, *, tenant_id=None, source_fingerprint=None):
        return {
            "tenant_id": tenant_id or run.tenant_id,
            "source_asset_id": run.source_asset_id,
            "source_fingerprint": source_fingerprint or run.source_fingerprint,
            "video_metadata_profile_id": run.video_metadata_profile_id,
            "metadata_profile": run.metadata_profile,
            "metadata_profile_version": run.metadata_profile_version,
            "prompt_version": run.prompt_version,
            "analysis_version": run.analysis_version,
            "ai_provider": run.ai_provider,
        }

    @staticmethod
    def _analyze_chunk(repository, run, chunk):
        repository.mark_chunk_preparing(tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunk.id)
        repository.mark_chunk_uploaded(tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunk.id)
        return repository.mark_chunk_analyzing(tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunk.id)

    def test_postgresql_expanded_run_lifecycle_and_compatibility(self):
        """Exercise compatible lookup and a persisted three-chunk resume on PostgreSQL."""
        with Session(self.engine, expire_on_commit=False) as session:
            asset, profile = self._prerequisites(session, self.tenant_id)
            repository = VideoSearchRepository(session)
            completed_values = self._run_values(self.tenant_id, asset.id, profile.id, source_fingerprint="c" * 64)
            completed_values.pop("idempotency_key")
            completed = repository.get_or_create_run(**completed_values)
            self.assertEqual(repository.get_or_create_run(**completed_values).id, completed.id)
            chunk = repository.create_chunks(
                tenant_id=self.tenant_id, run_id=completed.id,
                layouts=[{"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 1000}],
            )[0]
            self.assertEqual(completed.status, "pending")
            self.assertEqual(repository.mark_run_preparing(tenant_id=self.tenant_id, run_id=completed.id).status, "preparing")
            self.assertEqual(repository.mark_run_analyzing(tenant_id=self.tenant_id, run_id=completed.id).status, "analyzing")
            self._analyze_chunk(repository, completed, chunk)
            repository.complete_chunk(tenant_id=self.tenant_id, run_id=completed.id, chunk_id=chunk.id, metadata_json={"stable": True})
            repository.complete_chunk(tenant_id=self.tenant_id, run_id=completed.id, chunk_id=chunk.id, metadata_json={"stable": False})
            self.assertEqual(completed.completed_chunks, 1)
            self.assertEqual(repository.complete_run(tenant_id=self.tenant_id, run_id=completed.id).status, "completed")
            self.assertEqual(repository.find_completed_compatible_run(**self._identity(completed)).id, completed.id)
            self.assertEqual(repository.find_completed_compatible_run(**self._identity(completed)).ai_model, "flash")

            foreign_tenant = self.tenant_id + "-foreign"
            foreign_asset, foreign_profile = self._prerequisites(session, foreign_tenant)
            foreign_values = self._run_values(foreign_tenant, foreign_asset.id, foreign_profile.id, source_fingerprint="x" * 64)
            foreign_values.pop("idempotency_key")
            foreign_run = repository.get_or_create_run(**foreign_values)
            self.assertIsNone(repository.find_completed_compatible_run(**self._identity(foreign_run, tenant_id=self.tenant_id)))

            partial_values = self._run_values(self.tenant_id, asset.id, profile.id, source_fingerprint="p" * 64)
            partial_values.pop("idempotency_key")
            partial = repository.get_or_create_run(**partial_values)
            chunks = repository.create_chunks(
                tenant_id=self.tenant_id, run_id=partial.id,
                layouts=[
                    {"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 1000},
                    {"chunk_index": 1, "source_start_ms": 1000, "source_end_ms": 2000},
                    {"chunk_index": 2, "source_start_ms": 2000, "source_end_ms": 3000},
                ],
            )
            repository.mark_run_preparing(tenant_id=self.tenant_id, run_id=partial.id)
            repository.mark_run_analyzing(tenant_id=self.tenant_id, run_id=partial.id)
            for chunk in chunks[:2]:
                self._analyze_chunk(repository, partial, chunk)
                repository.complete_chunk(tenant_id=self.tenant_id, run_id=partial.id, chunk_id=chunk.id, metadata_json={"chunk": chunk.chunk_index})
            self._analyze_chunk(repository, partial, chunks[2])
            deferred = repository.defer_chunk(tenant_id=self.tenant_id, run_id=partial.id, chunk_id=chunks[2].id, error_code="quota", error_message="retry later")
            self.assertEqual((partial.completed_chunks, deferred.status), (2, "pending"))
            self.assertEqual(repository.find_resumable_compatible_run(**self._identity(partial)).id, partial.id)
            self.assertEqual(repository.find_resumable_compatible_run(**self._identity(partial)).ai_model, "flash")
            self.assertIsNone(repository.find_resumable_compatible_run(**self._identity(partial, tenant_id=foreign_tenant)))
            session.commit()
            completed_id, partial_id = completed.id, partial.id

        with Session(self.engine, expire_on_commit=False) as session:
            repository = VideoSearchRepository(session)
            completed = repository.get_run(tenant_id=self.tenant_id, run_id=completed_id)
            partial = repository.get_run(tenant_id=self.tenant_id, run_id=partial_id)
            self.assertEqual(completed.status, "completed")
            chunks = repository.list_chunks(tenant_id=self.tenant_id, run_id=partial.id)
            self.assertEqual(([chunk.status for chunk in chunks], partial.completed_chunks), (["completed", "completed", "pending"], 2))
            self._analyze_chunk(repository, partial, chunks[2])
            repository.complete_chunk(tenant_id=self.tenant_id, run_id=partial.id, chunk_id=chunks[2].id, metadata_json={"chunk": 2})
            self.assertEqual(partial.completed_chunks, 3)
            self.assertEqual(repository.complete_run(tenant_id=self.tenant_id, run_id=partial.id).status, "completed")
            session.commit()

    def test_postgresql_resumable_conflict_is_explicit(self):
        with Session(self.engine, expire_on_commit=False) as session:
            asset, profile = self._prerequisites(session, self.tenant_id)
            first = self._run_values(self.tenant_id, asset.id, profile.id, idempotency_key="1" * 64, source_fingerprint="m" * 64)
            second = self._run_values(self.tenant_id, asset.id, profile.id, idempotency_key="2" * 64, source_fingerprint="m" * 64)
            session.add_all([VideoAnalysisRunModel(**first), VideoAnalysisRunModel(**second)])
            session.flush()
            repository = VideoSearchRepository(session)
            probe = session.scalar(select(VideoAnalysisRunModel).where(VideoAnalysisRunModel.idempotency_key == "1" * 64))
            with self.assertRaises(VideoRunConflictError):
                repository.find_resumable_compatible_run(**self._identity(probe))
            session.commit()

    def test_postgresql_gemini_quota_reservation_is_atomic_at_project_limit(self):
        scope = self.tenant_id + "-quota"
        now = datetime(2040, 1, 1, tzinfo=timezone.utc)
        barrier = threading.Barrier(3)
        outcomes, errors = [], []
        guard = threading.Lock()

        def reserve(model):
            try:
                barrier.wait(timeout=10)
                with Session(self.engine) as session:
                    decision = GeminiProjectQuotaRepository(session).reserve_request(
                        quota_scope=scope, model=model, rpd=10, project_rpd=1, now=now
                    )
                    session.commit()
                    with guard:
                        outcomes.append(decision.allowed)
            except Exception as exc:
                with guard:
                    errors.append(exc)

        workers = [threading.Thread(target=reserve, args=(f"model-{index}",)) for index in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=10)
        for worker in workers:
            worker.join(timeout=20)
        self.assertEqual(errors, [])
        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(outcomes.count(False), 1)
        with Session(self.engine) as session:
            total = session.get(GeminiProjectQuotaStateModel, {"quota_scope": scope, "model": "__project_total__"})
            self.assertIsNotNone(total)
            self.assertEqual(total.reserved_requests, 1)

    def _assert_integrity(self, session, factory):
        with self.assertRaises(IntegrityError):
            with session.begin_nested():
                session.add(factory())
                session.flush()
        self.assertEqual(session.scalar(select(1)), 1)

    def test_postgresql_constraints_are_enforced_and_recoverable(self):
        with Session(self.engine, expire_on_commit=False) as session:
            asset_a, profile_a = self._prerequisites(session, self.tenant_id)
            asset_b, profile_b = self._prerequisites(session, self.tenant_id + "-b")
            valid = self._run_values(self.tenant_id, asset_a.id, profile_a.id)
            run = VideoAnalysisRunModel(**valid)
            session.add(run)
            session.flush()
            self._assert_integrity(session, lambda: VideoAnalysisRunModel(**self._run_values(
                self.tenant_id + "-b", asset_a.id, profile_b.id, idempotency_key="s" * 64
            )))
            self._assert_integrity(session, lambda: VideoAnalysisRunModel(**self._run_values(
                self.tenant_id + "-b", asset_b.id, profile_a.id, idempotency_key="p" * 64
            )))
            self._assert_integrity(session, lambda: VideoAnalysisRunModel(**self._run_values(
                self.tenant_id, asset_a.id, profile_a.id, idempotency_key=run.idempotency_key
            )))
            for field, value in (("status", "invalid"), ("attempt_count", -1),
                                 ("chunk_seconds", 0), ("total_chunks", -1),
                                 ("completed_chunks", -1), ("completed_chunks", 1),
                                 ("duration_ms", -1), ("source_width", 0), ("source_height", 0)):
                overrides = {field: value, "idempotency_key": (field + str(value)).ljust(64, "x")[:64]}
                if field == "completed_chunks" and value == 1:
                    overrides["total_chunks"] = 0
                self._assert_integrity(session, lambda overrides=overrides: VideoAnalysisRunModel(**self._run_values(
                    self.tenant_id, asset_a.id, profile_a.id, **overrides
                )))
            nullable = VideoAnalysisRunModel(**self._run_values(
                self.tenant_id, asset_a.id, profile_a.id, idempotency_key="n" * 64,
                duration_ms=None, source_width=None, source_height=None
            ))
            session.add(nullable)
            session.flush()
            self._assert_integrity(session, lambda: VideoAnalysisChunkModel(
                tenant_id=self.tenant_id + "-b", run_id=run.id, chunk_index=0,
                source_start_ms=0, source_end_ms=1, status="pending", attempt_count=0
            ))
            chunk = VideoAnalysisChunkModel(
                tenant_id=self.tenant_id, run_id=run.id, chunk_index=0,
                source_start_ms=0, source_end_ms=1, status="pending", attempt_count=0
            )
            session.add(chunk)
            session.flush()
            self._assert_integrity(session, lambda: VideoAnalysisChunkModel(
                tenant_id=self.tenant_id, run_id=run.id, chunk_index=0,
                source_start_ms=2, source_end_ms=3, status="pending", attempt_count=0
            ))
            for field, value in (("status", "invalid"), ("chunk_index", -1),
                                 ("source_start_ms", -1), ("source_end_ms", 0),
                                 ("attempt_count", -1), ("proxy_size_bytes", -1)):
                values = {"chunk_index": 10 + len(field), "source_start_ms": 0,
                          "source_end_ms": 1, "status": "pending", "attempt_count": 0}
                values[field] = value
                self._assert_integrity(session, lambda values=values: VideoAnalysisChunkModel(
                    tenant_id=self.tenant_id, run_id=run.id, **values
                ))
            session.add(VideoAnalysisChunkModel(
                tenant_id=self.tenant_id, run_id=run.id, chunk_index=99,
                source_start_ms=0, source_end_ms=1, status="pending", attempt_count=0,
                proxy_size_bytes=None
            ))
            session.rollback()

    def test_postgresql_repository_race_uses_integrity_fallback(self):
        with Session(self.engine, expire_on_commit=False) as setup:
            asset, profile = self._prerequisites(setup, self.tenant_id)
            setup.commit()
            values = {
                "tenant_id": self.tenant_id, "source_asset_id": asset.id,
                "source_fingerprint": "r" * 64, "video_metadata_profile_id": profile.id,
                "metadata_profile": profile.profile_name, "metadata_profile_version": profile.profile_version,
                "prompt_version": "p1", "analysis_version": "a1", "ai_provider": "gemini",
                "ai_model": "flash", "chunk_seconds": 30,
            }
        barrier = threading.Barrier(2)
        original = VideoSearchRepository.get_run_by_idempotency_key
        calls = {"count": 0}
        guard = threading.Lock()
        def synchronized_lookup(repository, **kwargs):
            with guard:
                calls["count"] += 1
                first_lookup = calls["count"] <= 2
            result = original(repository, **kwargs)
            if first_lookup:
                barrier.wait(timeout=10)
            return result
        VideoSearchRepository.get_run_by_idempotency_key = synchronized_lookup
        results, errors = [], []
        def caller():
            try:
                with Session(self.engine, expire_on_commit=False) as session:
                    result = VideoSearchRepository(session).get_or_create_run(**values)
                    session.commit()
                    results.append(result.id)
            except Exception as exc:
                errors.append(exc)
        try:
            threads = [threading.Thread(target=caller), threading.Thread(target=caller)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=20)
        finally:
            VideoSearchRepository.get_run_by_idempotency_key = original
        self.assertFalse(errors, errors)
        self.assertGreaterEqual(calls["count"], 3)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        with Session(self.engine) as session:
            self.assertEqual(session.query(VideoAnalysisRunModel).filter_by(
                tenant_id=self.tenant_id, idempotency_key=VideoSearchRepository(session).get_or_create_run(**values).idempotency_key
            ).count(), 1)


    def test_repository_reraises_unrelated_integrity_error(self):
        with Session(self.engine, expire_on_commit=False) as session:
            asset_a, _profile_a = self._prerequisites(session, self.tenant_id)
            _asset_b, profile_b = self._prerequisites(session, self.tenant_id + "-b")
            session.commit()
            with self.assertRaises(IntegrityError):
                VideoSearchRepository(session).get_or_create_run(
                    tenant_id=self.tenant_id + "-b", source_asset_id=asset_a.id,
                    source_fingerprint="u" * 64, video_metadata_profile_id=profile_b.id,
                    metadata_profile=profile_b.profile_name,
                    metadata_profile_version=profile_b.profile_version,
                    prompt_version="p1", analysis_version="a1",
                    ai_provider="gemini", ai_model="flash", chunk_seconds=30,
                )
            self.assertEqual(session.scalar(select(1)), 1)
