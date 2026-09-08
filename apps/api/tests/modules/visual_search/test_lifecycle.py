from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from app.core.config import Settings
from app.modules.visual_search.lifecycle import (
    VISUAL_EMBEDDING_SCHEMA_VERSION,
    enqueue_visual_index_sync,
    enqueue_visual_retire_sync,
)


class FakeProcessing:
    def __init__(self) -> None:
        self.jobs = {}

    def get_job_by_key(self, tenant_id: str, key: str):
        return self.jobs.get((tenant_id, key))

    def create_job(self, **kwargs):
        self.jobs[(kwargs["tenant_id"], kwargs["idempotency_key"])] = kwargs
        return kwargs


def enabled_settings() -> Settings:
    return Settings(
        PROCESSING_JOBS_ENABLED=True,
        VISUAL_SEARCH_ENABLED=True,
        ELASTICSEARCH_URL="http://elasticsearch.test",
    )


def test_visual_index_enqueue_is_disabled_by_default() -> None:
    processing = FakeProcessing()
    assert not enqueue_visual_index_sync(
        processing, settings=Settings(), tenant_id="tenant-a", asset_id="asset-a",
        source_asset_id="source-a", content_sha256="a" * 64,
    )
    assert not processing.jobs


def test_visual_index_enqueue_is_content_and_schema_idempotent() -> None:
    processing = FakeProcessing()
    settings = enabled_settings()
    assert enqueue_visual_index_sync(
        processing, settings=settings, tenant_id="tenant-a", asset_id="asset-a",
        source_asset_id="source-a", content_sha256="a" * 64,
    )
    assert not enqueue_visual_index_sync(
        processing, settings=settings, tenant_id="tenant-a", asset_id="asset-a",
        source_asset_id="source-a", content_sha256="a" * 64,
    )
    assert enqueue_visual_index_sync(
        processing, settings=settings, tenant_id="tenant-a", asset_id="asset-a",
        source_asset_id="source-a", content_sha256="b" * 64,
    )
    jobs = list(processing.jobs.values())
    assert len(jobs) == 2
    assert all(job["job_type"] == "visual_index_sync" for job in jobs)
    assert jobs[0]["payload"]["embedding_schema_version"] == VISUAL_EMBEDDING_SCHEMA_VERSION


def test_visual_retire_enqueue_is_idempotent_and_does_not_need_content() -> None:
    processing = FakeProcessing()
    settings = enabled_settings()
    assert enqueue_visual_retire_sync(
        processing, settings=settings, tenant_id="tenant-a", asset_id="asset-a",
        identity="source-retired",
    )
    assert not enqueue_visual_retire_sync(
        processing, settings=settings, tenant_id="tenant-a", asset_id="asset-a",
        identity="source-retired",
    )
    job = next(iter(processing.jobs.values()))
    assert job["payload"]["operation"] == "reconcile_retired_source"
