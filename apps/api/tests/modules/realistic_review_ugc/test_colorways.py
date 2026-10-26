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
from app.modules.realistic_review_ugc.model import RrugcColorwayJobModel, RrugcSourcePlanModel, RrugcImageOutputVersionModel, RrugcFrontPlacementSettingModel
from app.modules.realistic_review_ugc.colorways import (
    COLORS, PRODUCTS_COLORS, PRODUCTS_COLOR_KEYS, ColorwayError, ColorwayService, effective_status,
)
from app.modules.realistic_review_ugc import colorways
from app.modules.realistic_review_ugc.source_plans import RRUGC_SOURCE_ROOT_FOLDER_ID


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    for table in (
        RrugcSourcePlanModel.__table__, RrugcColorwayJobModel.__table__,
        ProcessingJobModel.__table__, TenantProcessingPolicyModel.__table__,
        RrugcImageOutputVersionModel.__table__, RrugcFrontPlacementSettingModel.__table__,
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
        id=source_id, tenant_id=tenant, root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID, source_file_id="drive-" + source_id,
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



def test_products_slot7_only_uses_twelve_natural_caps_and_keeps_retired_history(db):
    source = plan(db, name="design_01.png", source_id="products-12")
    source.root_folder_id = colorways.PRODUCTS_STAGE1_SELECTED_ROOT
    db.commit()
    from app.modules.realistic_review_ugc.front_placement import (
        DEFAULT_FRONT_PLACEMENT, save_front_placement,
    )
    customized = {**DEFAULT_FRONT_PLACEMENT, "top": 0.27}
    save_front_placement(db, source.tenant_id, "actor", customized, 0)
    service = ColorwayService(db)
    assert len(PRODUCTS_COLORS) == 12
    assert len(PRODUCTS_COLOR_KEYS) == 12
    assert "khaki-maroon" not in PRODUCTS_COLOR_KEYS
    assert service.readiness("products")["stock_total_count"] == 12
    assert service.readiness("reviews")["stock_total_count"] == 13
    assert service.queue(tenant_id=source.tenant_id, user_id="actor",
                         source_plan_ids=[source.id], workflow="products") == {
        "queued": 12, "existing": 0,
    }
    assert len(service.list(tenant_id=source.tenant_id, source_ids=[source.id])) == 12
    assert set(j.color_key for j in db.query(RrugcColorwayJobModel)) == PRODUCTS_COLOR_KEYS
    for row in db.query(RrugcColorwayJobModel):
        proc = db.get(ProcessingJobModel, row.processing_job_id)
        assert proc.payload_json["front_placement"] == {**customized, "design_size": 100}

    # A pre-existing 13th output is immutable but excluded from the live UI,
    # future queues, ZIP delivery and all subsequent Products generations.
    old = RrugcColorwayJobModel(
        id="legacy-khaki", tenant_id=source.tenant_id,
        source_plan_id=source.id, source_revision=source.source_revision,
        color_key="khaki-maroon", stock_sha256="a" * 64,
        skill_source="local", skill_name="legacy", status="completed",
        output_remote_file_id="keep-legacy-photo", created_by_user_id="actor",
    )
    db.add(old)
    db.commit()
    assert len(service.list(tenant_id=source.tenant_id, source_ids=[source.id])) == 12
    assert service.queue(tenant_id=source.tenant_id, user_id="actor",
                         source_plan_ids=[source.id], workflow="products") == {
        "queued": 0, "existing": 12,
    }
    with pytest.raises(ColorwayError, match="retired"):
        service.regenerate(tenant_id=source.tenant_id, job_id=old.id)
    old.status = "failed"
    db.commit()
    with pytest.raises(ColorwayError, match="retired"):
        service.retry(tenant_id=source.tenant_id, job_id=old.id)
    assert db.get(RrugcColorwayJobModel, old.id).output_remote_file_id == "keep-legacy-photo"
    assert db.query(RrugcColorwayJobModel).count() == 13
    assert db.query(ProcessingJobModel).count() == 12


def test_products_readiness_ignores_retired_stock_but_reviews_still_requires_it(db, monkeypatch):
    import app.modules.realistic_review_ugc.colorways as mod
    stock = mod._stock_bytes
    def only_old_missing(settings, key):
        if key == "khaki-maroon":
            raise ColorwayError("colorway_stock_missing", "Retired", 503)
        return stock(settings, key)
    monkeypatch.setattr(mod, "_stock_bytes", only_old_missing)
    svc = ColorwayService(db)
    assert svc.readiness("products") == {
        "ready": True, "error_code": None, "stock_ready_count": 12, "stock_total_count": 12,
    }
    assert svc.readiness("reviews") == {
        "ready": False, "error_code": "colorway_stock_missing",
        "stock_ready_count": 12, "stock_total_count": 13,
    }

def test_queue_13_colors_durable_idempotent_and_tenant_scoped(db):
    source = plan(db)
    svc = ColorwayService(db)
    with pytest.raises(ColorwayError, match="Only available JPG/PNG"):
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
    with pytest.raises(ColorwayError, match="Only available JPG/PNG"):
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


def test_regenerate_re_pins_changed_stock_but_preserves_original_output(db, monkeypatch):
    source = plan(db)
    svc = ColorwayService(db)
    svc.queue(tenant_id=source.tenant_id, user_id="actor", source_plan_ids=[source.id])
    jobs = db.query(RrugcColorwayJobModel).order_by(RrugcColorwayJobModel.color_key).all()
    changed, untouched = jobs[0], jobs[1]
    changed.status = "completed"
    changed.output_remote_file_id = "original-remote-id"
    changed.output_content_type = "image/png"
    changed.output_size_bytes = 1234
    changed.output_width = changed.output_height = 1024
    old_hash = changed.stock_sha256
    unchanged_hash = untouched.stock_sha256
    db.commit()

    monkeypatch.setattr(colorways, "_stock_bytes",
                        lambda settings, key: (key + ":new-front").encode())
    queued = svc.regenerate(tenant_id=source.tenant_id, job_id=changed.id)
    assert queued.status == "queued"
    assert queued.stock_sha256 != old_hash
    assert queued.stock_sha256 == __import__("hashlib").sha256(
        (changed.color_key + ":new-front").encode()).hexdigest()
    assert queued.output_remote_file_id == "original-remote-id"
    assert queued.processing_job_id is not None
    assert db.get(RrugcColorwayJobModel, untouched.id).stock_sha256 == unchanged_hash
    assert db.query(ProcessingJobModel).count() == 14

    # The next successful output preserves the former file as version 1.
    from app.modules.realistic_review_ugc.output_versions import save_output_version
    revision = save_output_version(
        db, tenant_id=source.tenant_id, stage="stage2", job=queued,
        remote_file_id="new-output-file", content_type="image/png",
        size_bytes=2048, width=1254, height=1254, processing_job_id=queued.processing_job_id,
    )
    assert revision == 2
    history = db.query(RrugcImageOutputVersionModel).filter_by(
        tenant_id=source.tenant_id, job_id=queued.id, stage="stage2",
    ).order_by(RrugcImageOutputVersionModel.version).all()
    assert [v.remote_file_id for v in history] == ["original-remote-id", "new-output-file"]


def test_reconcile_census_only_flags_stale_completed_products_jobs(db, monkeypatch):
    from scripts import cam_slot7_stock_reconcile as cli

    source = plan(db)
    source.root_folder_id = colorways.PRODUCTS_STAGE1_SELECTED_ROOT
    db.commit()
    svc = ColorwayService(db)
    svc.queue(tenant_id=source.tenant_id, user_id="actor",
              source_plan_ids=[source.id], workflow="products")
    all_jobs = db.query(RrugcColorwayJobModel).order_by(RrugcColorwayJobModel.color_key).all()
    for row in all_jobs:
        row.status = "completed"
        row.output_remote_file_id = "preserve:" + row.color_key
    all_jobs[0].status = "running"
    image = BytesIO()
    Image.new("RGB", (1200, 1200), "#dddddd").save(image, "JPEG")
    data = image.getvalue()
    monkeypatch.setattr(cli, "_stock_bytes", lambda settings, color: data)
    monkeypatch.setattr(cli, "read_managed_stock", lambda settings, color, view: (Path("trusted.jpg"), "abc"))
    all_jobs[1].stock_sha256 = __import__("hashlib").sha256(data).hexdigest()
    db.commit()

    report, stale = cli.inspect(db, svc.settings)
    assert report["color_count"] == 12
    assert report["products_colorway_jobs"] == 12
    assert report["existing_output_files"] == 12
    assert report["stale_completed_jobs"] == 10
    assert report["matching_jobs"] == 1
    assert all(row.status == "completed" and row.output_remote_file_id for row in stale)
    assert all(row.id not in {all_jobs[0].id, all_jobs[1].id} for row in stale)
    assert db.query(ProcessingJobModel).count() == 12


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


@pytest.mark.parametrize("products_mode", [False, True])
def test_worker_preserves_generated_color_and_is_idempotent(db, monkeypatch, products_mode):
    """Run the real handler through its storage and worker boundaries without a provider call."""
    canvas = Image.new("RGB", (640, 640), "white")
    output = BytesIO()
    canvas.save(output, "PNG")
    image_bytes = output.getvalue()
    # New Products jobs use a deterministic embroidered-front reference.
    design_canvas = Image.new("RGBA", (240, 120), (0, 0, 0, 0))
    from PIL import ImageDraw
    ImageDraw.Draw(design_canvas).rectangle((30, 20, 210, 100), fill="blue")
    design_output = BytesIO()
    design_canvas.save(design_output, "PNG")
    design_bytes = design_output.getvalue()
    monkeypatch.setattr(colorways, "_stock_bytes", lambda settings, key: image_bytes)
    monkeypatch.setattr(colorways, "verify_stage2_skill_runtime", lambda **kwargs: None)

    source = plan(db, name="design_01.png" if products_mode else "embroidery_design.png")
    if products_mode:
        source.root_folder_id = colorways.PRODUCTS_STAGE1_SELECTED_ROOT
        db.commit()
    svc = ColorwayService(db)
    svc.queue(tenant_id="tenant-a", user_id="actor", source_plan_ids=[source.id],
              workflow="products" if products_mode else "reviews")
    job = db.query(RrugcColorwayJobModel).first()
    generated_calls = []
    saved_assets = []

    class FakeStream:
        async def _body(self):
            yield design_bytes if products_mode else image_bytes
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
            assert references[0].role == "design_reference"
            assert len(references) == (2 if products_mode else 1)
            if products_mode:
                assert references[1].role == "embroidered_front_preview"
            assert "Official cap color: " + dict(COLORS)[job.color_key] in prompt
            assert ("canonical-design-direct-thread-v4" in prompt) is products_mode
            if products_mode:
                assert "TWELVE STOCK COLORS" in prompt
                assert "PRE-SAVE VISUAL QUALITY CHECK" in prompt
            return SimpleNamespace(
                image_bytes=image_bytes, mime_type="image/png", provider_request_id="test-provider",
            )
        def cleanup_attempt(self, attempt_id):
            pass

    monkeypatch.setattr(colorways, "CodexImageGenRunner", FakeRunner)
    from app.modules.realistic_review_ugc import products_run_all
    monkeypatch.setattr(products_run_all, "advance_after_output", lambda *args, **kwargs: None)
    from contextlib import nullcontext
    monkeypatch.setattr(
        colorways, "acquire_worker_profile",
        lambda *args, **kwargs: nullcontext(("account-1", "/tmp/test-codex")),
    )
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

def test_reviews_stage2_filters_exact_embroidery_prefix_and_products_selected_namespace(db):
    from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT

    other = plan(db, name="ART DEPARTMENT.JPG", source_id="other-design")
    nested = plan(db, name="Embroidery_02.png", source_id="review-design")
    products = plan(db, name="design_01.png", source_id="product-design")
    products.root_folder_id = PRODUCTS_STAGE1_SELECTED_ROOT
    db.commit()
    svc = ColorwayService(db)
    with pytest.raises(ColorwayError, match="Only available JPG/PNG"):
        svc.queue(tenant_id="tenant-a", user_id="actor",
                  source_plan_ids=[other.id], workflow="reviews")
    with pytest.raises(ColorwayError, match="Only available JPG/PNG"):
        svc.queue(tenant_id="tenant-a", user_id="actor",
                  source_plan_ids=[products.id], workflow="reviews")
    with pytest.raises(ColorwayError, match="Only available JPG/PNG"):
        svc.queue(tenant_id="tenant-a", user_id="actor",
                  source_plan_ids=[nested.id], workflow="products")

    assert svc.queue(tenant_id="tenant-a", user_id="actor",
                     source_plan_ids=[nested.id], workflow="reviews")["queued"] == 13
    assert svc.queue(tenant_id="tenant-a", user_id="actor",
                     source_plan_ids=[products.id], workflow="products")["queued"] == 12
    assert db.query(RrugcColorwayJobModel).count() == 25


def test_stage2_rejects_non_image_and_wrong_root(db):
    other_root = plan(db, name="design.jpg", source_id="wrong-root")
    other_root.root_folder_id = "other-folder"
    bad_type = plan(db, name="design.webp", source_id="unsupported-file")
    bad_type.source_mime_type = "image/webp"
    db.commit()
    for row in (other_root, bad_type):
        with pytest.raises(ColorwayError, match="Only available JPG/PNG"):
            ColorwayService(db).queue(tenant_id="tenant-a", user_id="actor",
                                     source_plan_ids=[row.id])