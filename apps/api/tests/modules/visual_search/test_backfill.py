from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.modules.visual_search.backfill import VisualSearchBackfillService
from app.modules.visual_search.lifecycle import VISUAL_EMBEDDING_SCHEMA_VERSION


class FakeResult:
    def __init__(self, rows): self.rows = rows
    def all(self): return list(self.rows)


class FakeSession:
    def __init__(self, pages): self.pages = list(pages)
    def execute(self, _statement):
        return FakeResult(self.pages.pop(0) if self.pages else [])


class FakeProcessing:
    def __init__(self, session, existing=()): self.session = session; self.existing = set(existing)
    def get_job_by_key(self, _tenant_id, key): return key if key in self.existing else None


def asset(asset_id, mime_type="image/jpeg", content_hash="a" * 64):
    return (
        SimpleNamespace(id=asset_id, content_hash=content_hash),
        SimpleNamespace(id="source-" + asset_id, mime_type=mime_type),
    )


def test_dry_run_is_bounded_and_returns_resume_checkpoint():
    session = FakeSession([[asset("a"), asset("b")], [asset("c")]])
    result = VisualSearchBackfillService(FakeProcessing(session), settings=Settings(VISUAL_SEARCH_ENABLED=True, VISUAL_SEARCH_CANARY_TENANT_IDS="tenant-a")).run(
        tenant_id="tenant-a", schema_version=VISUAL_EMBEDDING_SCHEMA_VERSION,
        batch_size=2, max_assets=2,
    )
    assert result.scanned == 2
    assert result.enqueued == 2
    assert result.checkpoint_asset_id == "b"


def test_execute_requires_the_explicit_backfill_flag():
    service = VisualSearchBackfillService(FakeProcessing(FakeSession([])), settings=Settings(VISUAL_SEARCH_ENABLED=True, VISUAL_SEARCH_CANARY_TENANT_IDS="tenant-a"))
    with pytest.raises(ValueError, match="backfill is disabled"):
        service.run(
            tenant_id="tenant-a", schema_version=VISUAL_EMBEDDING_SCHEMA_VERSION,
            dry_run=False,
        )


def test_unsupported_and_missing_hash_are_skipped():
    session = FakeSession([[asset("a", content_hash=""), asset("b", mime_type="video/mp4")]])
    result = VisualSearchBackfillService(FakeProcessing(session), settings=Settings(VISUAL_SEARCH_ENABLED=True, VISUAL_SEARCH_CANARY_TENANT_IDS="tenant-a")).run(
        tenant_id="tenant-a", schema_version=VISUAL_EMBEDDING_SCHEMA_VERSION,
        max_assets=2,
    )
    assert result.enqueued == 0
    assert result.skipped_missing_hash == 1
    assert result.skipped_unsupported == 1


def test_dry_run_skips_existing_idempotency_key():
    from app.modules.visual_search.lifecycle import visual_index_job_key
    key = visual_index_job_key("a", "a" * 64)
    service = VisualSearchBackfillService(FakeProcessing(FakeSession([[asset("a")]]), [key]), settings=Settings(VISUAL_SEARCH_ENABLED=True, VISUAL_SEARCH_CANARY_TENANT_IDS="tenant-a"))
    result = service.run(tenant_id="tenant-a", schema_version=VISUAL_EMBEDDING_SCHEMA_VERSION, max_assets=1)
    assert result.enqueued == 0
    assert result.skipped_existing == 1


@pytest.mark.parametrize("dry_run", [True, False])
def test_non_canary_tenant_backfill_never_scans_or_enqueues(dry_run: bool):
    session = FakeSession([[asset("a")]])
    service = VisualSearchBackfillService(
        FakeProcessing(session),
        settings=Settings(
            VISUAL_SEARCH_ENABLED=True,
            VISUAL_SEARCH_CANARY_TENANT_IDS="tenant-a",
            VISUAL_SEARCH_BACKFILL_ENABLED=True,
        ),
    )
    with pytest.raises(ValueError, match="tenant is not eligible"):
        service.run(
            tenant_id="tenant-b",
            schema_version=VISUAL_EMBEDDING_SCHEMA_VERSION,
            dry_run=dry_run,
        )
    assert len(session.pages) == 1
