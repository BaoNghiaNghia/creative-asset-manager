import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.video_search.model import VideoMetadataProfileModel
from app.modules.video_search.repository import (
    VideoChunkLayoutConflictError,
    VideoSearchRepository,
    VideoStateTransitionError,
)


class VideoSearchRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        if hasattr(self, "session"):
            self.session.close()
        self.engine.dispose()

    def repository(self):
        self.session = self.sessions()
        return VideoSearchRepository(self.session)

    def profile(self, repository, *, tenant_id="tenant-a", active=True, name="video"):
        profile = VideoMetadataProfileModel(
            tenant_id=tenant_id,
            profile_name=name,
            profile_version="v1",
            prompt_template="describe video",
            active=active,
        )
        repository.session.add(profile)
        repository.session.flush()
        return profile

    def source_asset(self, repository, *, tenant_id="tenant-a", asset_id="source-asset"):
        assets = AssetRegistryRepository(repository.session)
        source = assets.upsert_external_source(
            tenant_id=tenant_id,
            source_key=f"source-{asset_id}",
            source_type="google_drive",
        )
        return assets.upsert_source_asset(
            tenant_id=tenant_id,
            external_source_id=source.id,
            external_asset_id=asset_id,
            filename="clip.mp4",
            mime_type="video/mp4",
            size_bytes=100,
            provider_checksum="checksum",
            provider_version="version",
        )

    def create_run(self, repository, *, tenant_id="tenant-a", fingerprint="f" * 64):
        profile = self.profile(repository, tenant_id=tenant_id)
        asset = self.source_asset(repository, tenant_id=tenant_id)
        return repository.get_or_create_run(
            tenant_id=tenant_id,
            source_asset_id=asset.id,
            source_fingerprint=fingerprint,
            video_metadata_profile_id=profile.id,
            metadata_profile=profile.profile_name,
            metadata_profile_version=profile.profile_version,
            prompt_version="prompt-v1",
            analysis_version="analysis-v1",
            ai_provider="google-gemini",
            ai_model="gemini-2.5-flash",
            chunk_seconds=30,
        )

    def prepare_analyzing(self, repository, run):
        repository.mark_run_preparing(tenant_id=run.tenant_id, run_id=run.id)
        return repository.mark_run_analyzing(tenant_id=run.tenant_id, run_id=run.id)

    def prepare_chunk_analyzing(self, repository, run, chunk):
        repository.mark_chunk_preparing(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunk.id
        )
        repository.mark_chunk_uploaded(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunk.id,
            proxy_size_bytes=42,
        )
        return repository.mark_chunk_analyzing(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunk.id
        )

    def test_profile_reads_are_tenant_scoped_and_active_is_deterministic(self):
        repository = self.repository()
        first = self.profile(repository, name="first")
        second = self.profile(repository, name="second")
        foreign = self.profile(repository, tenant_id="tenant-b", name="foreign")

        self.assertIs(repository.get_profile(tenant_id="tenant-a", profile_id=first.id), first)
        self.assertIsNone(repository.get_profile(tenant_id="tenant-b", profile_id=first.id))
        self.assertIs(repository.get_active_profile(tenant_id="tenant-a"), second)
        self.assertIs(repository.get_active_profile(tenant_id="tenant-b"), foreign)

    def test_compatible_run_lookups_are_tenant_scoped(self):
        repository = self.repository()
        completed = self.create_run(repository, tenant_id="tenant-b")
        self.prepare_analyzing(repository, completed)
        repository.complete_run(tenant_id="tenant-b", run_id=completed.id)
        resumable = repository.get_or_create_run(
            tenant_id="tenant-b", source_asset_id=completed.source_asset_id,
            source_fingerprint="e" * 64, video_metadata_profile_id=completed.video_metadata_profile_id,
            metadata_profile=completed.metadata_profile, metadata_profile_version=completed.metadata_profile_version,
            prompt_version=completed.prompt_version, analysis_version=completed.analysis_version,
            ai_provider=completed.ai_provider, ai_model="model-b", chunk_seconds=30,
        )
        identity = {"tenant_id": "tenant-a", "source_asset_id": completed.source_asset_id,
            "source_fingerprint": completed.source_fingerprint, "video_metadata_profile_id": completed.video_metadata_profile_id,
            "metadata_profile": completed.metadata_profile, "metadata_profile_version": completed.metadata_profile_version,
            "prompt_version": completed.prompt_version, "analysis_version": completed.analysis_version, "ai_provider": completed.ai_provider}
        self.assertIsNone(repository.find_completed_compatible_run(**identity))
        identity["source_fingerprint"] = resumable.source_fingerprint
        self.assertIsNone(repository.find_resumable_compatible_run(**identity))

    def test_get_or_create_run_is_idempotent_and_identity_scoped(self):
        repository = self.repository()
        run = self.create_run(repository)
        same = repository.get_or_create_run(
            tenant_id=run.tenant_id, source_asset_id=run.source_asset_id,
            source_fingerprint=run.source_fingerprint,
            video_metadata_profile_id=run.video_metadata_profile_id,
            metadata_profile=run.metadata_profile,
            metadata_profile_version=run.metadata_profile_version,
            prompt_version=run.prompt_version, analysis_version=run.analysis_version,
            ai_provider=run.ai_provider, ai_model=run.ai_model, chunk_seconds=30,
        )
        changed = repository.get_or_create_run(
            tenant_id=run.tenant_id, source_asset_id=run.source_asset_id,
            source_fingerprint="e" * 64,
            video_metadata_profile_id=run.video_metadata_profile_id,
            metadata_profile=run.metadata_profile,
            metadata_profile_version=run.metadata_profile_version,
            prompt_version=run.prompt_version, analysis_version=run.analysis_version,
            ai_provider=run.ai_provider, ai_model=run.ai_model, chunk_seconds=30,
        )

        self.assertEqual(run.id, same.id)
        self.assertNotEqual(run.id, changed.id)
        self.assertIs(repository.get_run(tenant_id="tenant-a", run_id=run.id), run)
        self.assertIsNone(repository.get_run(tenant_id="tenant-b", run_id=run.id))
        self.assertIsNone(repository.get_run_by_idempotency_key(
            tenant_id="tenant-b", idempotency_key=run.idempotency_key
        ))

    def test_run_state_machine_and_completion_progress_guard(self):
        repository = self.repository()
        run = self.create_run(repository)
        chunks = repository.create_chunks(
            tenant_id=run.tenant_id, run_id=run.id,
            layouts=[
                {"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 10},
                {"chunk_index": 1, "source_start_ms": 10, "source_end_ms": 20},
            ],
        )
        self.prepare_analyzing(repository, run)
        with self.assertRaises(VideoStateTransitionError):
            repository.complete_run(tenant_id=run.tenant_id, run_id=run.id)
        self.prepare_chunk_analyzing(repository, run, chunks[0])
        repository.complete_chunk(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunks[0].id,
            metadata_json={"chunk": 0},
        )
        self.prepare_chunk_analyzing(repository, run, chunks[1])
        repository.complete_chunk(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunks[1].id,
            metadata_json={"chunk": 1},
        )
        self.assertEqual(repository.complete_run(
            tenant_id=run.tenant_id, run_id=run.id, summary_json={"done": True}
        ).status, "completed")
        with self.assertRaises(VideoStateTransitionError):
            repository.mark_run_preparing(tenant_id=run.tenant_id, run_id=run.id)

    def test_run_failure_retry_cancel_and_terminal_guards(self):
        repository = self.repository()
        run = self.create_run(repository)
        repository.fail_run(
            tenant_id=run.tenant_id, run_id=run.id, error_code="E", error_message="failure"
        )
        self.assertEqual(run.status, "failed")
        repository.mark_run_preparing(tenant_id=run.tenant_id, run_id=run.id)
        self.assertEqual(run.attempt_count, 1)
        repository.fail_run(
            tenant_id=run.tenant_id, run_id=run.id, error_code="E2", error_message="failure"
        )
        repository.cancel_run(tenant_id=run.tenant_id, run_id=run.id)
        self.assertEqual(run.status, "cancelled")
        self.assertIsNotNone(run.completed_at)
        with self.assertRaises(VideoStateTransitionError):
            repository.fail_run(
                tenant_id=run.tenant_id, run_id=run.id, error_code="E3", error_message="failure"
            )

    def test_chunk_layout_is_idempotent_and_conflicts_are_explicit(self):
        repository = self.repository()
        run = self.create_run(repository)
        layout = [{"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 10}]
        first = repository.create_chunks(tenant_id=run.tenant_id, run_id=run.id, layouts=layout)
        second = repository.create_chunks(tenant_id=run.tenant_id, run_id=run.id, layouts=layout)
        self.assertEqual([chunk.id for chunk in first], [chunk.id for chunk in second])
        self.assertEqual(run.total_chunks, 1)
        with self.assertRaises(VideoChunkLayoutConflictError):
            repository.create_chunks(
                tenant_id=run.tenant_id, run_id=run.id,
                layouts=[{"chunk_index": 0, "source_start_ms": 1, "source_end_ms": 10}],
            )
        with self.assertRaises(VideoChunkLayoutConflictError):
            repository.create_chunks(
                tenant_id=run.tenant_id, run_id=run.id,
                layouts=[
                    {"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 10},
                    {"chunk_index": 0, "source_start_ms": 10, "source_end_ms": 20},
                ],
            )

    def test_chunk_completion_is_exactly_once_and_metadata_is_immutable(self):
        repository = self.repository()
        run = self.create_run(repository)
        chunks = repository.create_chunks(
            tenant_id=run.tenant_id, run_id=run.id,
            layouts=[
                {"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 10},
                {"chunk_index": 1, "source_start_ms": 10, "source_end_ms": 20},
            ],
        )
        for chunk in chunks:
            self.prepare_chunk_analyzing(repository, run, chunk)
        completed = repository.complete_chunk(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunks[0].id,
            metadata_json={"canonical": True}, usage_json={"tokens": 1},
        )
        self.assertEqual(run.completed_chunks, 1)
        repeated = repository.complete_chunk(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunks[0].id,
            metadata_json={"canonical": False}, usage_json={"tokens": 2},
        )
        self.assertIs(repeated, completed)
        self.assertEqual(run.completed_chunks, 1)
        self.assertEqual(completed.metadata_json, {"canonical": True})
        repository.complete_chunk(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunks[1].id,
            metadata_json={"second": True},
        )
        self.assertEqual(run.completed_chunks, 2)
        repository.complete_chunk(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunks[1].id,
            metadata_json={"second": False},
        )
        self.assertEqual(run.completed_chunks, run.total_chunks)

    def test_chunk_state_machine_and_tenant_isolation(self):
        repository = self.repository()
        run = self.create_run(repository)
        chunk = repository.create_chunks(
            tenant_id=run.tenant_id, run_id=run.id,
            layouts=[{"chunk_index": 0, "source_start_ms": 0, "source_end_ms": 10}],
        )[0]
        self.assertEqual(repository.list_chunks(tenant_id="tenant-b", run_id=run.id), [])
        with self.assertRaises(LookupError):
            repository.mark_run_preparing(tenant_id="tenant-b", run_id=run.id)
        with self.assertRaises(LookupError):
            repository.mark_chunk_preparing(
                tenant_id="tenant-b", run_id=run.id, chunk_id=chunk.id
            )
        with self.assertRaises(VideoStateTransitionError):
            repository.mark_chunk_uploaded(
                tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunk.id
            )
        self.prepare_chunk_analyzing(repository, run, chunk)
        repository.fail_chunk(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunk.id,
            error_code="E", error_message="failure",
        )
        repository.mark_chunk_preparing(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunk.id
        )
        repository.mark_chunk_uploaded(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunk.id
        )
        repository.mark_chunk_analyzing(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunk.id
        )
        repository.complete_chunk(
            tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunk.id, metadata_json={}
        )
        with self.assertRaises(VideoStateTransitionError):
            repository.fail_chunk(
                tenant_id=run.tenant_id, run_id=run.id, chunk_id=chunk.id,
                error_code="E", error_message="failure",
            )
