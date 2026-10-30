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



def _history_fixture(tmp_path, monkeypatch, *, slots=(1, 2)):
    """Exercise real SQLite checkpoints and a fake streaming storage adapter."""
    from io import BytesIO
    from uuid import uuid4

    from PIL import Image, ImageDraw
    from app.domain.providers.contracts import StoredAsset
    from app.modules.realistic_review_ugc import typography_output_history as writer

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (RrugcKeywordVolumeModel, RrugcImageOutputVersionModel, AuthAuditEventModel):
        model.__table__.create(engine)
    keyword_id = str(uuid4())
    batch_id = str(uuid4())
    with Session(engine) as session:
        session.add(RrugcKeywordVolumeModel(
            id=keyword_id, tenant_id="tenant-a",
            keyword="HELLO", keyword_normalized="hello",
            picked=True,
        ))
        session.commit()

    images = {}
    for slot in slots:
        artwork = Image.new("RGBA", (2048, 1024), (0, 0, 0, 0))
        ImageDraw.Draw(artwork).rectangle(
            (220 + 10 * slot, 210, 1740 - 10 * slot, 790),
            fill=(25 + slot, 60, 100, 255),
        )
        buf = BytesIO()
        artwork.save(buf, format="PNG")
        data = buf.getvalue()
        (tmp_path / f"design_{slot:02}.png").write_bytes(data)
        images[slot] = data

    plan = {"source_keyword_exact": "HELLO"}
    plan_sha = "a" * 64
    monkeypatch.setattr(writer, "resolve_plan", lambda _session, **kw: plan)
    monkeypatch.setattr(writer, "fingerprint", lambda value: plan_sha)
    monkeypatch.setattr(writer, "_existing_batch_dir", lambda *_args: tmp_path)

    def inspected(**params):
        selected = sorted(params["selected_slots"])
        assert all(slot in images for slot in selected)
        return {
            "plan_sha256": plan_sha,
            "source_keyword_exact": "HELLO",
            "selection_preview": {
                "selection_sha256": "b" * 64,
                "selected_images": [
                    {"slot": slot, "sha256": sha256(images[slot]).hexdigest(),
                     "size_bytes": len(images[slot])}
                    for slot in selected
                ],
            },
        }

    monkeypatch.setattr(writer, "inspect_reviewed_batch", inspected)

    class MemoryStorage:
        def __init__(self):
            self.calls = []
            self.fail_on = set()

        async def store_asset(self, request):
            slot = int(request.filename.split("_")[-1][:-4])
            if slot in self.fail_on:
                raise StorageProviderError(
                    "Simulated interrupted managed upload",
                    code="simulated_upload_failure", retryable=True,
                )
            data = b"".join([part async for part in request.body])
            assert data == images[slot]
            assert sha256(data).hexdigest() == request.content_hash
            self.calls.append((slot, request.asset_id))
            return StoredAsset(
                storage_key=request.asset_id,
                remote_file_id=f"remote-{slot}",
                content_hash=request.content_hash,
                size_bytes=len(data),
            )

    from app.domain.providers.contracts import StorageProviderError

    def factory():
        return Session(engine, autoflush=False, expire_on_commit=False)

    async def save(selected, storage):
        return await writer.save_reviewed_v2_outputs(
            settings=flags(TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED=True),
            tenant_id="tenant-a", user_id="operator",
            keyword_id=keyword_id, reference_id="reference-a",
            batch_id=batch_id, selected_slots=selected,
            session_factory=factory, storage=storage,
        )

    return engine, keyword_id, batch_id, MemoryStorage(), save


@pytest.mark.asyncio
async def test_private_history_saves_genuine_partial_and_replays_idempotently(tmp_path, monkeypatch):
    engine, keyword_id, batch_id, storage, save = _history_fixture(
        tmp_path, monkeypatch, slots=(1, 2, 3),
    )
    try:
        first = await save([1, 3], storage)
        assert first["saved_count"] == 2
        assert first["new_count"] == 2
        assert first["stage2_jobs_created"] == 0
        assert first["published"] is False
        assert first["private_only"] is True
        assert [r["slot"] for r in first["items"]] == [1, 3]

        again = await save([3, 1], storage)
        assert again["saved_count"] == 2
        assert again["new_count"] == 0
        assert len(storage.calls) == 2  # Retry does not upload again.
        with Session(engine) as session:
            saved = list(session.scalars(select(RrugcImageOutputVersionModel).where(
                RrugcImageOutputVersionModel.tenant_id == "tenant-a",
                RrugcImageOutputVersionModel.stage == HISTORY_STAGE,
            ).order_by(RrugcImageOutputVersionModel.version.asc())))
            assert len(saved) == 2
            assert [r.version for r in saved] == [1, 2]
            assert [r.output_name for r in saved] == ["design_01.png", "design_03.png"]
            assert all(r.processing_job_id == batch_id for r in saved)
            assert all(r.job_id == keyword_id for r in saved)
            events = list(session.scalars(select(AuthAuditEventModel)))
            assert len(events) == 2
            assert all(e.action == "typography_v2.output_saved" for e in events)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_private_history_upload_failure_retains_committed_first_slot(tmp_path, monkeypatch):
    engine, keyword_id, _, storage, save = _history_fixture(tmp_path, monkeypatch)
    try:
        storage.fail_on.add(2)
        from app.domain.providers.contracts import StorageProviderError
        with pytest.raises(StorageProviderError):
            await save([1, 2], storage)
        with Session(engine) as session:
            rows = list(session.scalars(select(RrugcImageOutputVersionModel)))
            assert len(rows) == 1
            assert rows[0].output_name == "design_01.png"
        storage.fail_on.clear()
        recovered = await save([1, 2], storage)
        assert recovered["saved_count"] == 2
        assert recovered["new_count"] == 1
        assert [slot for slot, _ in storage.calls] == [1, 2]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_private_history_revoked_keyword_prevents_upload(tmp_path, monkeypatch):
    engine, keyword_id, _, storage, save = _history_fixture(tmp_path, monkeypatch)
    try:
        with Session(engine) as session:
            row = session.get(RrugcKeywordVolumeModel, keyword_id)
            row.picked = False
            session.commit()
        with pytest.raises(TypographyPilotError) as exc:
            await save([1], storage)
        assert exc.value.code == "source_changed"
        assert storage.calls == []
        with Session(engine) as session:
            assert session.scalar(select(RrugcImageOutputVersionModel)) is None
    finally:
        engine.dispose()
