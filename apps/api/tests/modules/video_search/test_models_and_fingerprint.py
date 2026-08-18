import re
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

import app.modules.assets.model  # Register source_assets metadata for the video FK.
from app.modules.video_search.fingerprint import (
    build_video_analysis_idempotency_key,
    build_video_source_fingerprint,
)
from app.modules.video_search.model import (
    VideoAnalysisChunkModel,
    VideoAnalysisRunModel,
    VideoMetadataProfileModel,
)


HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def source_asset(**overrides):
    state = {
        "external_source_id": "source-1",
        "external_asset_id": "asset-1",
        "provider_checksum": "checksum-1",
        "provider_version": "version-1",
        "source_modified_at": datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc),
        "size_bytes": 1024,
        "mime_type": "video/mp4",
        "filename": "clip.mp4",
        "source_created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "source_metadata": {"width": 1920},
        "last_seen_generation": 1,
        "last_seen_at": datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc),
        "deleted_at": None,
        "created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 17, 11, 0, tzinfo=timezone.utc),
        "hashed_provider_checksum": "hashed-checksum-1",
        "hashed_provider_version": "hashed-version-1",
    }
    state.update(overrides)
    return SimpleNamespace(**state)


def identity_values(**overrides):
    values = {
        "tenant_id": "tenant-1",
        "source_asset_id": "asset-1",
        "source_fingerprint": "a" * 64,
        "video_metadata_profile_id": "profile-1",
        "metadata_profile_version": "v1",
        "prompt_version": "prompt-v1",
        "analysis_version": "analysis-v1",
        "ai_provider": "google-gemini",
        "ai_model": "gemini-2.5-flash",
    }
    values.update(overrides)
    return values


def names(table, kind):
    return {item.name for item in table.constraints if isinstance(item, kind)}


class VideoFingerprintTest(unittest.TestCase):
    def test_source_fingerprint_is_deterministic_sha256(self):
        first = build_video_source_fingerprint(source_asset())
        second = build_video_source_fingerprint(source_asset())

        self.assertEqual(first, second)
        self.assertIsInstance(first, str)
        self.assertRegex(first, HEX_SHA256)

    def test_source_fingerprint_changes_for_stable_identity_fields(self):
        baseline = build_video_source_fingerprint(source_asset())
        changes = {
            "provider_checksum": "checksum-2",
            "provider_version": "version-2",
            "source_modified_at": datetime(2026, 8, 17, 10, 1, tzinfo=timezone.utc),
            "size_bytes": 2048,
            "mime_type": "video/quicktime",
        }

        for field, changed_value in changes.items():
            with self.subTest(field=field):
                self.assertNotEqual(
                    baseline,
                    build_video_source_fingerprint(source_asset(**{field: changed_value})),
                )

    def test_source_fingerprint_ignores_volatile_fields(self):
        baseline = build_video_source_fingerprint(source_asset())
        changes = {
            "filename": "renamed.mp4",
            "source_created_at": datetime(2026, 8, 2, tzinfo=timezone.utc),
            "source_metadata": {"width": 3840, "height": 2160},
            "last_seen_generation": 2,
            "last_seen_at": datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc),
            "deleted_at": datetime(2026, 8, 18, tzinfo=timezone.utc),
            "created_at": datetime(2026, 8, 2, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 8, 18, tzinfo=timezone.utc),
            "hashed_provider_checksum": "hashed-checksum-2",
            "hashed_provider_version": "hashed-version-2",
        }

        for field, changed_value in changes.items():
            with self.subTest(field=field):
                self.assertEqual(
                    baseline,
                    build_video_source_fingerprint(source_asset(**{field: changed_value})),
                )

    def test_source_fingerprint_changes_for_source_location_identity(self):
        baseline = build_video_source_fingerprint(source_asset())

        self.assertNotEqual(
            baseline,
            build_video_source_fingerprint(source_asset(external_source_id="source-2")),
        )
        self.assertNotEqual(
            baseline,
            build_video_source_fingerprint(source_asset(external_asset_id="asset-2")),
        )

    def test_source_fingerprint_normalizes_equivalent_aware_datetimes(self):
        utc_value = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
        ict_value = utc_value.astimezone(timezone(timedelta(hours=7)))

        self.assertEqual(
            build_video_source_fingerprint(source_asset(source_modified_at=utc_value)),
            build_video_source_fingerprint(source_asset(source_modified_at=ict_value)),
        )

    def test_analysis_idempotency_key_is_deterministic_sha256(self):
        first = build_video_analysis_idempotency_key(**identity_values())
        second = build_video_analysis_idempotency_key(**identity_values())

        self.assertEqual(first, second)
        self.assertIsInstance(first, str)
        self.assertRegex(first, HEX_SHA256)

    def test_analysis_idempotency_key_changes_for_each_identity_field(self):
        baseline = build_video_analysis_idempotency_key(**identity_values())
        fields = (
            "tenant_id",
            "source_asset_id",
            "source_fingerprint",
            "video_metadata_profile_id",
            "metadata_profile_version",
            "prompt_version",
            "analysis_version",
            "ai_provider",
            "ai_model",
        )

        for field in fields:
            with self.subTest(field=field):
                value = identity_values()[field]
                self.assertNotEqual(
                    baseline,
                    build_video_analysis_idempotency_key(
                        **identity_values(**{field: f"changed-{value}"})
                    ),
                )

    def test_analysis_idempotency_key_serializes_nullable_fields_deterministically(self):
        values = identity_values(ai_provider=None, ai_model=None)

        self.assertEqual(
            build_video_analysis_idempotency_key(**values),
            build_video_analysis_idempotency_key(**values),
        )


class VideoModelMetadataTest(unittest.TestCase):
    def test_video_model_table_names_and_no_image_model_reuse(self):
        self.assertEqual(VideoMetadataProfileModel.__tablename__, "video_metadata_profiles")
        self.assertEqual(VideoAnalysisRunModel.__tablename__, "video_analysis_runs")
        self.assertEqual(VideoAnalysisChunkModel.__tablename__, "video_analysis_chunks")

        video_tables = {
            VideoMetadataProfileModel.__tablename__,
            VideoAnalysisRunModel.__tablename__,
            VideoAnalysisChunkModel.__tablename__,
        }
        self.assertTrue(video_tables.isdisjoint({
            "assets", "asset_source_links", "asset_ai_analyses", "metadata_profiles",
        }))

    def test_profile_metadata(self):
        table = VideoMetadataProfileModel.__table__
        self.assertTrue({
            "id", "tenant_id", "profile_name", "profile_version", "prompt_template",
            "optional_json_schema", "search_config_json", "active", "created_at", "updated_at",
        }.issubset(table.c.keys()))
        self.assertTrue({
            "uq_video_metadata_profiles_tenant_name_version",
            "uq_video_metadata_profiles_tenant_id",
        }.issubset(names(table, UniqueConstraint)))
        self.assertIn("ix_video_metadata_profiles_active", {index.name for index in table.indexes})

    def test_run_metadata(self):
        table = VideoAnalysisRunModel.__table__
        self.assertTrue({
            "id", "tenant_id", "source_asset_id", "source_fingerprint",
            "video_metadata_profile_id", "metadata_profile", "metadata_profile_version",
            "prompt_version", "analysis_version", "ai_provider", "ai_model", "idempotency_key",
            "status", "duration_ms", "source_width", "source_height", "chunk_seconds",
            "total_chunks", "completed_chunks", "summary_json", "attempt_count",
            "last_error_code", "last_error_message", "created_at", "updated_at", "started_at", "completed_at",
        }.issubset(table.c.keys()))
        self.assertTrue({
            "fk_video_analysis_runs_tenant_source_asset",
            "fk_video_analysis_runs_tenant_profile",
        }.issubset(names(table, ForeignKeyConstraint)))
        self.assertTrue({
            "uq_video_analysis_runs_tenant_id",
            "uq_video_analysis_runs_tenant_idempotency",
        }.issubset(names(table, UniqueConstraint)))
        self.assertTrue({
            "ck_video_analysis_runs_status", "ck_video_analysis_runs_attempt_count",
            "ck_video_analysis_runs_chunk_seconds", "ck_video_analysis_runs_total_chunks",
            "ck_video_analysis_runs_completed_chunks", "ck_video_analysis_runs_chunk_progress",
            "ck_video_analysis_runs_duration", "ck_video_analysis_runs_width",
            "ck_video_analysis_runs_height",
        }.issubset(names(table, CheckConstraint)))
        self.assertTrue({
            "ix_video_analysis_runs_source_history", "ix_video_analysis_runs_status_created",
            "ix_video_analysis_runs_fingerprint",
        }.issubset({index.name for index in table.indexes}))

    def test_chunk_metadata(self):
        table = VideoAnalysisChunkModel.__table__
        self.assertTrue({
            "id", "tenant_id", "run_id", "chunk_index", "source_start_ms", "source_end_ms",
            "status", "proxy_size_bytes", "provider_file_name", "provider_file_uri",
            "metadata_json", "usage_json", "provider_metadata_json", "attempt_count",
            "last_error_code", "last_error_message", "created_at", "updated_at", "started_at", "completed_at",
        }.issubset(table.c.keys()))
        self.assertIn("fk_video_analysis_chunks_tenant_run", names(table, ForeignKeyConstraint))
        self.assertTrue({
            "uq_video_analysis_chunks_run_index", "uq_video_analysis_chunks_tenant_id",
        }.issubset(names(table, UniqueConstraint)))
        self.assertTrue({
            "ck_video_analysis_chunks_status", "ck_video_analysis_chunks_index",
            "ck_video_analysis_chunks_start", "ck_video_analysis_chunks_range",
            "ck_video_analysis_chunks_attempt_count", "ck_video_analysis_chunks_proxy_size",
        }.issubset(names(table, CheckConstraint)))
        self.assertTrue({
            "ix_video_analysis_chunks_run", "ix_video_analysis_chunks_status",
        }.issubset({index.name for index in table.indexes}))
