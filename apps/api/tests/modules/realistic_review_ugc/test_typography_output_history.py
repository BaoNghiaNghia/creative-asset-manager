"""G6.3 private V2 history: isolation, idempotency and failure gates."""
from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordVolumeModel,
)
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.realistic_review_ugc.typography_output_history import (
    HISTORY_STAGE, history_enabled, _asset_id, _assert_history_row,
    list_v2_history,
)
from app.modules.realistic_review_ugc.typography_imagegen_pilot import TypographyPilotError


def flags(**kwargs):
    d = dict(TYPOGRAPHY_V2_ENABLED=True,
        TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED=True,
        TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED=True,
        IMAGE_GENERATION_ENABLED=True,
        CODEX_IMAGE_GENERATION_ENABLED=True,
        MANAGED_ASSET_STORAGE_ENABLED=True,
        TYPOGRAPHY_V2_CANARY_TENANT_IDS="tenant-a",
        TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED=False)
    d.update(kwargs)
    return SimpleNamespace(**d)


def test_writer_has_separate_off_by_default_gate():
    assert history_enabled(flags(), "tenant-a") is False
    assert history_enabled(flags(TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED=True), "tenant-a")
    assert not history_enabled(flags(TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED=True), "tenant-b")
    assert not history_enabled(flags(TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED=True,
                                     MANAGED_ASSET_STORAGE_ENABLED=False), "tenant-a")
    assert not history_enabled(flags(TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED=True,
                                     TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED=False), "tenant-a")


def test_stable_asset_identity_is_scoped_to_tenant_keyword_batch_slot():
    a = _asset_id("tenant-a", "keyword", "batch", 1)
    assert a == _asset_id("tenant-a", "keyword", "batch", 1)
    assert a != _asset_id("tenant-b", "keyword", "batch", 1)
    assert a != _asset_id("tenant-a", "other", "batch", 1)
    assert a != _asset_id("tenant-a", "keyword", "batch", 2)


def test_history_projection_never_exposes_storage_ids_or_cross_tenant():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    RrugcImageOutputVersionModel.__table__.create(engine)
    with Session(engine) as session:
        session.add_all([
            RrugcImageOutputVersionModel(
                tenant_id="tenant-a", stage=HISTORY_STAGE, job_id="keyword-a",
                version=1, processing_job_id="batch-a", output_name="design_01.png",
                font_info_json={"slot": 1}, remote_file_id="private-drive-id",
                content_type="image/png", size_bytes=100, width=2048, height=1024),
            RrugcImageOutputVersionModel(
                tenant_id="tenant-b", stage=HISTORY_STAGE, job_id="keyword-a",
                version=1, processing_job_id="batch-b", output_name="design_01.png",
                font_info_json={"slot": 1}, remote_file_id="other-private-drive-id",
                content_type="image/png", size_bytes=100, width=2048, height=1024),
            RrugcImageOutputVersionModel(
                tenant_id="tenant-a", stage="stage1", job_id="keyword-a",
                version=1, processing_job_id="v1", output_name="output_001.png",
                remote_file_id="v1-id"),
        ])
        session.commit()
        result = list_v2_history(session, tenant_id="tenant-a", keyword_id="keyword-a")
        assert len(result) == 1
        assert result[0]["slot"] == 1
        assert "private-drive-id" not in str(result)
        assert "v1-id" not in str(result)
        assert session.scalar(select(RrugcImageOutputVersionModel).where(
            RrugcImageOutputVersionModel.stage == "stage1")).remote_file_id == "v1-id"
    engine.dispose()


def test_checkpoint_identity_must_match_reviewed_png():
    row = RrugcImageOutputVersionModel(
        tenant_id="tenant-a", stage=HISTORY_STAGE, job_id="keyword-a",
        version=1, processing_job_id="batch-a", output_name="design_01.png",
        font_info_json={"schema_version": "typography-v2/output-history-v1",
                        "slot": 1, "png_sha256": "a" * 64,
                        "plan_sha256": "b" * 64},
        remote_file_id="stored-1", content_type="image/png",
        size_bytes=100, width=2048, height=1024)
    _assert_history_row(row, slot=1, image_sha256="a"*64,
                        size_bytes=100, plan_sha256="b"*64)
    with pytest.raises(TypographyPilotError):
        _assert_history_row(row, slot=2, image_sha256="a"*64,
                            size_bytes=100, plan_sha256="b"*64)
    with pytest.raises(TypographyPilotError):
        _assert_history_row(row, slot=1, image_sha256="c"*64,
                            size_bytes=100, plan_sha256="b"*64)


@pytest.mark.asyncio
async def test_disabled_writer_never_reads_db_or_uploads():
    from app.modules.realistic_review_ugc.typography_output_history import save_reviewed_v2_outputs
    class Storage:
        async def store_asset(self, request):
            pytest.fail("Unexpected upload")
    def factory():
        pytest.fail("Unexpected DB")
    with pytest.raises(TypographyPilotError) as exc:
        await save_reviewed_v2_outputs(
            settings=flags(), tenant_id="tenant-a", user_id="operator",
            keyword_id="keyword-a", reference_id="ref", batch_id="batch",
            selected_slots=[1], session_factory=factory, storage=Storage())
    assert exc.value.code == "history_disabled"
