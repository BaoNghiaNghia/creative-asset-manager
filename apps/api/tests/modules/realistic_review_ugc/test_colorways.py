from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from sqlalchemy.orm import sessionmaker

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.realistic_review_ugc.model import RrugcColorwayJobModel, RrugcSourcePlanModel, RrugcImageOutputVersionModel
from app.modules.realistic_review_ugc.colorways import (
    COLORS, ColorwayError, ColorwayService, effective_status,
)
from app.modules.realistic_review_ugc import colorways


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    for table in (
        RrugcSourcePlanModel.__table__, RrugcColorwayJobModel.__table__,
        ProcessingJobModel.__table__, TenantProcessingPolicyModel.__table__,
        RrugcImageOutputVersionModel.__table__,
    ):
        table.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def skill_stubs(monkeypatch):
    settings = get_settings().model_copy(update={
        "PROCESSING_JOBS_ENABLED": True, "IMAGE_GENERATION_ENABLED": True,
        "MANAGED_ASSET_STORAGE_ENABLED": True, "CODEX_IMAGE_GENERATION_ENABLED": True,
    })
    monkeypatch.setattr(colorways, "get_settings", lambda: settings)
    skill = SimpleNamespace(source="local", skill_id=None, skill_name="test-skill", skill_version="v1")
    monkeypatch.setattr(colorways, "resolve_stage2_skill", lambda **kwargs: skill)
    monkeypatch.setattr(colorways, "ensure_skill_registry", lambda *args, **kwargs: None)
    monkeypatch.setattr(colorways, "assert_skill_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(colorways, "installed_stage2_skill_sha256", lambda *args, **kwargs: "a" * 64)
    monkeypatch.setattr(colorways, "_stock_bytes", lambda settings, key: key.encode())
    monkeypatch.setattr(colorways, "_codex_runtime_available", lambda settings: True)


def plan(db, tenant="tenant-a", *, name="embroidery_design.png", source_id="plan-a"):
    row = RrugcSourcePlanModel(
        id=source_id, tenant_id=tenant, root_folder_id="root", source_file_id="drive-" + source_id,
        source_parent_folder_id="folder", source_relative_path="Folder/" + name,
        source_name=name, source_mime_type="image/png", source_revision="rev-1",
        created_by_user_id="actor", created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    return row


def test_canonical_color_names_match_shipped_stock_manifest():
    root = Path(__file__).resolve().parents[5]
    manifest = (root / "deploy/codex/skills/gatorhats-8869-image-studio"
                / "references/STOCK_MANIFEST.md").read_text(encoding="utf-8")
    for color_key, label in COLORS:
        assert f"| {label} | `assets/stock/{color_key}/` |" in manifest
        assert (root / "deploy/codex/skills/gatorhats-8869-image-studio"
                / "assets/stock" / color_key / "front.jpg").is_file()
    assert len(COLORS) == 13


def test_queue_13_colors_durable_idempotent_and_tenant_scoped(db):
    source = plan(db)
    svc = ColorwayService(db)
    with pytest.raises(ColorwayError, match="Only available embroidery"):
        svc.queue(tenant_id="tenant-b", user_id="actor", source_plan_ids=[source.id])
    result = svc.queue(tenant_id="tenant-a", user_id="actor", source_plan_ids=[source.id])
    assert result == {"queued": 13, "existing": 0}
    assert len(svc.list(tenant_id="tenant-a", source_ids=[source.id])) == 13
    assert svc.list(tenant_id="tenant-b", source_ids=[source.id]) == []
    again = svc.queue(tenant_id="tenant-a", user_id="actor", source_plan_ids=[source.id])
    assert again == {"queued": 0, "existing": 13}
    rows = db.query(RrugcColorwayJobModel).all()
    assert {row.color_key for row in rows} == {key for key, _ in COLORS}
    assert all(row.processing_job_id for row in rows)
    assert db.query(ProcessingJobModel).count() == 13


def test_stage2_readiness_reflects_stock_and_provider_without_queueing(db, monkeypatch):
    service = ColorwayService(db)
    assert service.readiness() == {
        "ready": True, "error_code": None, "stock_ready_count": 13,
        "stock_total_count": 13,
    }
    actual_stock = colorways._stock_bytes

    def missing_stock(settings, color):
        if color == "natural-navy":
            raise ColorwayError("colorway_stock_missing", "Missing", 503)
        return actual_stock(settings, color)

    monkeypatch.setattr(colorways, "_stock_bytes", missing_stock)
    assert service.readiness() == {
        "ready": False, "error_code": "colorway_stock_missing",
        "stock_ready_count": 12, "stock_total_count": 13,
    }
    disabled = service.settings.model_copy(update={"CODEX_IMAGE_GENERATION_ENABLED": False})
    status = ColorwayService(db, settings=disabled).readiness()
    assert status["ready"] is False
    assert status["error_code"] == "colorway_disabled"
    assert db.query(ProcessingJobModel).count() == 0


def test_disabled_colorway_provider_does_not_leave_permanently_queued_jobs(db):
    source = plan(db)
    settings = get_settings().model_copy(update={
        "PROCESSING_JOBS_ENABLED": True, "IMAGE_GENERATION_ENABLED": True,
        "MANAGED_ASSET_STORAGE_ENABLED": True, "CODEX_IMAGE_GENERATION_ENABLED": False,
    })
    with pytest.raises(ColorwayError, match="not enabled") as exc:
        ColorwayService(db, settings=settings).queue(
            tenant_id=source.tenant_id, user_id="actor", source_plan_ids=[source.id],
        )
    assert exc.value.status_code == 503
    assert db.query(RrugcColorwayJobModel).count() == 0
    assert db.query(ProcessingJobModel).count() == 0


def test_readiness_rejects_missing_runtime_even_with_all_stocks(db, monkeypatch):
    monkeypatch.setattr(colorways, "_codex_runtime_available", lambda settings: False)
    status = ColorwayService(db).readiness()
    assert status == {
        "ready": False, "error_code": "colorway_runtime_missing",
        "stock_ready_count": 13, "stock_total_count": 13,
    }
    assert db.query(ProcessingJobModel).count() == 0


def test_reject_non_embroidery_input_and_missing_sources(db):
    source = plan(db, name="output_previous.png")
    with pytest.raises(ColorwayError, match="Only available embroidery"):
        ColorwayService(db).queue(tenant_id=source.tenant_id, user_id="actor", source_plan_ids=[source.id])
    assert db.query(ProcessingJobModel).count() == 0


def test_retry_one_color_does_not_touch_other_outputs(db):
    source = plan(db)
    svc = ColorwayService(db)
    svc.queue(tenant_id=source.tenant_id, user_id="actor", source_plan_ids=[source.id])
    rows = db.query(RrugcColorwayJobModel).order_by(RrugcColorwayJobModel.color_key).all()
    failed, completed = rows[0], rows[1]
    failed.status = "failed"
    failed_processing = db.get(ProcessingJobModel, failed.processing_job_id)
    failed_processing.status = "failed"
    completed.status = "completed"
    completed.output_remote_file_id = "keep-output"
    db.commit()
    with pytest.raises(ColorwayError, match="Colorway job not found"):
        svc.retry(tenant_id="another-tenant", job_id=failed.id)
    with pytest.raises(ColorwayError, match="Only failed"):
        svc.retry(tenant_id=source.tenant_id, job_id=completed.id)
    retried = svc.retry(tenant_id=source.tenant_id, job_id=failed.id)
    assert retried.retry_count == 1
    assert retried.processing_job_id != failed_processing.id
    assert db.get(RrugcColorwayJobModel, completed.id).output_remote_file_id == "keep-output"
    assert effective_status(retried, db.get(ProcessingJobModel, retried.processing_job_id)) == "queued"
    assert db.query(ProcessingJobModel).count() == 14


def test_new_source_revision_keeps_old_output_and_queues_new_colors(db):
    source = plan(db)
    svc = ColorwayService(db)
    svc.queue(tenant_id=source.tenant_id, user_id="actor", source_plan_ids=[source.id])
    old = db.query(RrugcColorwayJobModel).first()
    old.status = "completed"
    old.output_remote_file_id = "historical-output"
    source.source_revision = "rev-2"
    db.commit()
    assert svc.list(tenant_id=source.tenant_id, source_ids=[source.id]) == []
    result = svc.queue(tenant_id=source.tenant_id, user_id="actor", source_plan_ids=[source.id])
    assert result["queued"] == 13
    assert db.get(RrugcColorwayJobModel, old.id).output_remote_file_id == "historical-output"
    assert db.query(RrugcColorwayJobModel).count() == 26
    assert len(svc.list(tenant_id=source.tenant_id, source_ids=[source.id])) == 13


def test_worker_preserves_generated_color_and_is_idempotent(db, monkeypatch):
    """Run the real handler through its storage and worker boundaries without a provider call."""
    canvas = Image.new("RGB", (640, 640), "white")
    output = BytesIO()
    canvas.save(output, "PNG")
    image_bytes = output.getvalue()
    monkeypatch.setattr(colorways, "_stock_bytes", lambda settings, key: image_bytes)
    monkeypatch.setattr(colorways, "verify_stage2_skill_runtime", lambda **kwargs: None)

    source = plan(db)
    svc = ColorwayService(db)
    svc.queue(tenant_id="tenant-a", user_id="actor", source_plan_ids=[source.id])
    job = db.query(RrugcColorwayJobModel).first()
    generated_calls = []
    saved_assets = []

    class FakeStream:
        async def _body(self):
            yield image_bytes
        @property
        def body(self):
            return self._body()
        async def close(self):
            pass

    class FakeStorage:
        async def open_asset(self, request):
            assert request.tenant_id == "tenant-a"
            assert request.remote_file_id == "drive-" + source.id
            return FakeStream()
        async def store_asset(self, request):
            saved_assets.append(request)
            return SimpleNamespace(remote_file_id="stored-output", remote_folder_id="folder")

    class FakeRunner:
        def __init__(self, config):
            self.config = config
        async def generate_from_references(self, *, attempt_id, person, references, prompt):
            generated_calls.append(attempt_id)
            assert person.image_bytes
            assert len(references) == 1 and references[0].role == "design_reference"
            assert "Official cap color: " + dict(COLORS)[job.color_key] in prompt
            return SimpleNamespace(
                image_bytes=image_bytes, mime_type="image/png", provider_request_id="test-provider",
            )
        def cleanup_attempt(self, attempt_id):
            pass

    monkeypatch.setattr(colorways, "CodexImageGenRunner", FakeRunner)
    dependencies = SimpleNamespace(
        session_factory=sessionmaker(bind=db.get_bind()),
        storage_provider=FakeStorage(),
    )
    context = SimpleNamespace(
        job=SimpleNamespace(
            job_type="rrugc_colorway_generate", entity_id=job.id, tenant_id="tenant-a",
            payload={"colorway_job_id": job.id},
        ),
        dependencies=dependencies, is_cancelled=False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None),
    )
    settings = SimpleNamespace(
        PROCESSING_JOBS_ENABLED=True, IMAGE_GENERATION_ENABLED=True, MANAGED_ASSET_STORAGE_ENABLED=True, CODEX_IMAGE_GENERATION_ENABLED=True,
    )
    handler = colorways.ColorwayGenerateHandler(settings)
    result = handler(context)
    assert result.outcome.value == "completed"
    db.expire_all()
    completed = db.get(RrugcColorwayJobModel, job.id)
    assert completed.status == "completed"
    assert completed.output_remote_file_id == "stored-output"
    assert saved_assets[0].destination_folder_id == "folder"
    assert saved_assets[0].filename.startswith("output_" + job.color_key + "_")
    assert len(saved_assets) == len(generated_calls) == 1
    assert handler(context).outcome.value == "completed"
    assert len(saved_assets) == len(generated_calls) == 1


def test_source_revision_change_blocks_retry(db):
    source = plan(db)
    svc = ColorwayService(db)
    svc.queue(tenant_id=source.tenant_id, user_id="actor", source_plan_ids=[source.id])
    job = db.query(RrugcColorwayJobModel).first()
    job.status = "failed"
    db.get(ProcessingJobModel, job.processing_job_id).status = "failed"
    source.source_revision = "new-revision"
    db.commit()
    with pytest.raises(ColorwayError, match="Source design changed"):
        svc.retry(tenant_id=source.tenant_id, job_id=job.id)
