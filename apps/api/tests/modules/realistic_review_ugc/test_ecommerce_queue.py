"""End-to-end SQL-backed queue contract without calling external image services."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.cap_stack_model import RrugcCapStackImageModel
from app.modules.realistic_review_ugc.ecommerce_pipeline import (
    EcommerceService, EcommerceError,
)
from app.modules.realistic_review_ugc.model import (
    RrugcSourcePlanModel, RrugcEcommerceJobModel,
    RrugcFrontPlacementSettingModel,
)
from app.modules.realistic_review_ugc.stage2_skills import ResolvedStage2Skill


@pytest.fixture
def ecommerce_db(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        RrugcSourcePlanModel.__table__, RrugcEcommerceJobModel.__table__,
        ProcessingJobModel.__table__, TenantProcessingPolicyModel.__table__,
        RrugcCapStackImageModel.__table__, RrugcFrontPlacementSettingModel.__table__,
    ])
    import app.modules.realistic_review_ugc.ecommerce_pipeline as pipeline
    monkeypatch.setattr(pipeline, "_assigned_skill", lambda *args: ResolvedStage2Skill(
        source="local", skill_id=None, skill_name="test-ecommerce-skill", skill_version=None))
    monkeypatch.setattr(pipeline, "installed_stage2_skill_sha256",
                        lambda *args, **kwargs: "f" * 64)
    from types import SimpleNamespace
    settings = SimpleNamespace(
        PROCESSING_JOBS_ENABLED=True, IMAGE_GENERATION_ENABLED=True,
        MANAGED_ASSET_STORAGE_ENABLED=True, CODEX_IMAGE_GENERATION_ENABLED=True,
    )
    with Session(engine) as session:
        session.add(RrugcSourcePlanModel(
            id="plan-001", tenant_id="tenant-001",
            root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="remote-design-file-id",
            source_relative_path="design.png",
            source_name="design.png", source_mime_type="image/png",
            source_revision="rev1", created_by_user_id="operator",
        ))
        session.commit()
        yield session, settings
    engine.dispose()


def test_queue_is_idempotent_and_job_is_durable(ecommerce_db):
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    first = service.queue(tenant_id="tenant-001", user_id="operator",
                          source_plan_id="plan-001", stage="prod_s2_ec1")
    second = service.queue(tenant_id="tenant-001", user_id="operator",
                           source_plan_id="plan-001", stage="prod_s2_ec1")
    assert first["already_exists"] is False
    assert second["already_exists"] is True
    assert first["job_id"] == second["job_id"]
    rows = list(session.scalars(select(RrugcEcommerceJobModel)))
    assert len(rows) == 1
    assert rows[0].processing_job_id
    assert rows[0].source_revision == "rev1"
    assert len(rows[0].stock_sha256) == 64
    jobs = list(session.scalars(select(ProcessingJobModel)))
    assert len(jobs) == 1 and jobs[0].job_type == "rrugc_ecommerce_generate"
    assert jobs[0].payload_json["ecommerce_job_id"] == first["job_id"]
    assert jobs[0].priority == 96


def test_front_placement_is_pinned_for_new_job_and_regenerate_uses_updated_setting(ecommerce_db):
    from app.modules.realistic_review_ugc.front_placement import (
        DEFAULT_FRONT_PLACEMENT, save_front_placement,
    )
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    saved = {**DEFAULT_FRONT_PLACEMENT, "left": 0.12, "top": 0.28}
    save_front_placement(session, "tenant-001", "operator", saved, 0)
    created = service.queue(tenant_id="tenant-001", user_id="operator",
                            source_plan_id="plan-001", stage="prod_s2_ec1")
    job = session.get(RrugcEcommerceJobModel, created["job_id"])
    first_processing = session.get(ProcessingJobModel, job.processing_job_id)
    assert first_processing.payload_json["front_placement"] == saved
    newer = {**saved, "left": 0.23}
    save_front_placement(session, "tenant-001", "operator", newer, 1)
    # Config changes cannot alter the already queued attempt.
    assert first_processing.payload_json["front_placement"] == saved
    job.status = "completed"
    job.output_remote_file_id = "old-image-stays-saved"
    job.front_preview_remote_file_id = "old-preview"
    session.commit()
    service.regenerate(tenant_id="tenant-001", job_id=job.id)
    assert job.output_remote_file_id == "old-image-stays-saved"
    assert job.front_preview_remote_file_id is None
    latest = session.get(ProcessingJobModel, job.processing_job_id)
    assert latest.payload_json["front_placement"] == newer


def test_three_quarter_and_side_require_completed_front(ecommerce_db):
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    for stage in ("prod_s2_ec2", "prod_s2_ec3"):
        with pytest.raises(EcommerceError, match="Skill 1"):
            service.queue(tenant_id="tenant-001", user_id="operator",
                          source_plan_id="plan-001", stage=stage)


def test_unembroidered_back_has_own_independent_job(ecommerce_db):
    session, settings = ecommerce_db
    result = EcommerceService(session, settings).queue(
        tenant_id="tenant-001", user_id="operator",
        source_plan_id="plan-001", stage="prod_s2_ec4")
    assert result["status"] == "queued"





def test_retry_creates_a_new_processing_attempt_without_duplicating_slot(ecommerce_db):
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    first = service.queue(tenant_id="tenant-001", user_id="operator",
                          source_plan_id="plan-001", stage="prod_s2_ec1")
    job = session.get(RrugcEcommerceJobModel, first["job_id"])
    processing = session.get(ProcessingJobModel, job.processing_job_id)
    job.status = "failed"
    processing.status = "failed"
    session.commit()
    original_processing = job.processing_job_id
    result = service.retry(tenant_id="tenant-001", job_id=job.id)
    assert result["retry_count"] == 1 and result["status"] == "queued"
    assert job.processing_job_id != original_processing
    assert session.scalar(select(__import__("sqlalchemy").func.count()).select_from(
        ProcessingJobModel)) == 2
    with pytest.raises(EcommerceError, match="Only failed"):
        service.retry(tenant_id="tenant-001", job_id=job.id)


def test_regenerate_retains_previous_output_and_queues_new_attempt(ecommerce_db):
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    first = service.queue(tenant_id="tenant-001", user_id="operator",
                          source_plan_id="plan-001", stage="prod_s2_ec1")
    job = session.get(RrugcEcommerceJobModel, first["job_id"])
    job.status = "completed"
    job.output_remote_file_id = "previous-image"
    session.commit()
    old_processing = job.processing_job_id
    result = service.regenerate(tenant_id="tenant-001", job_id=job.id)
    assert result["status"] == "queued"
    assert job.output_remote_file_id == "previous-image"
    assert job.processing_job_id != old_processing


def test_angle_stage_can_queue_after_front_output_persisted(ecommerce_db):
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    first = service.queue(tenant_id="tenant-001", user_id="operator",
                          source_plan_id="plan-001", stage="prod_s2_ec1")
    job = session.get(RrugcEcommerceJobModel, first["job_id"])
    job.status = "completed"
    job.output_remote_file_id = "front-remote"
    session.commit()
    for stage in ("prod_s2_ec2", "prod_s2_ec3"):
        result = service.queue(tenant_id="tenant-001", user_id="operator",
                               source_plan_id="plan-001", stage=stage)
        assert result["status"] == "queued" and result["already_exists"] is False
        child = session.get(RrugcEcommerceJobModel, result["job_id"])
        assert child.front_reference_remote_file_id == "front-remote"




def test_run_all_queues_front_back_and_durable_dependent_intent(ecommerce_db):
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    result = service.queue_all(tenant_id="tenant-001", user_id="operator",
                               source_plan_id="plan-001")
    states = {step["stage"]: step["status"] for step in result["steps"]}
    assert states == {
        "prod_s2_ec1": "queued",
        "prod_s2_ec2": "waiting_for_front",
        "prod_s2_ec3": "waiting_for_front",
        "prod_s2_ec4": "queued",
    }
    jobs = list(session.scalars(select(RrugcEcommerceJobModel)))
    assert len(jobs) == 2
    front = next(j for j in jobs if j.stage == "prod_s2_ec1")
    processing = session.get(ProcessingJobModel, front.processing_job_id)
    assert processing.payload_json["run_all_user_id"] == "operator"
    assert processing.payload_json["run_all_followups"] == [
        "prod_s2_ec2", "prod_s2_ec3"]
    again = service.queue_all(tenant_id="tenant-001", user_id="operator",
                              source_plan_id="plan-001")
    assert len(list(session.scalars(select(RrugcEcommerceJobModel)))) == 2
    assert again["steps"][1]["status"] == "waiting_for_front"


def test_run_all_front_completion_queues_angles_even_without_browser(ecommerce_db):
    from types import SimpleNamespace
    from app.modules.realistic_review_ugc.ecommerce_pipeline import EcommerceGenerateHandler
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    response = service.queue_all(tenant_id="tenant-001", user_id="operator",
                                 source_plan_id="plan-001")
    front = session.get(RrugcEcommerceJobModel, response["steps"][0]["job_id"])
    front.status = "completed"
    front.output_remote_file_id = "front-output-remote-id"
    session.commit()
    engine = session.get_bind()
    def session_factory():
        return Session(engine)
    context = SimpleNamespace(
        job=SimpleNamespace(tenant_id="tenant-001"),
        dependencies=SimpleNamespace(session_factory=session_factory),
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None,
                               exception=lambda *args, **kwargs: None),
    )
    EcommerceGenerateHandler(settings)._dispatch_run_all_followups(
        context, front.id, front.processing_job_id)
    rows = list(session.scalars(select(RrugcEcommerceJobModel)))
    assert {row.stage for row in rows} == {
        "prod_s2_ec1", "prod_s2_ec2", "prod_s2_ec3", "prod_s2_ec4"}
    for row in rows:
        if row.stage in ("prod_s2_ec2", "prod_s2_ec3"):
            assert row.front_reference_remote_file_id == "front-output-remote-id"
    # Worker replay and button double click cannot enqueue extra jobs.
    EcommerceGenerateHandler(settings)._dispatch_run_all_followups(
        context, front.id, front.processing_job_id)
    service.queue_all(tenant_id="tenant-001", user_id="operator",
                      source_plan_id="plan-001")
    assert len(list(session.scalars(select(RrugcEcommerceJobModel)))) == 4
    assert len(list(session.scalars(select(ProcessingJobModel)))) == 4




def test_multihat_slot_pins_one_twenty_photo_selection_across_three_shots(ecommerce_db, monkeypatch):
    """Scene and color palettes are pinned per design; retry never randomizes."""
    from io import BytesIO
    from PIL import Image
    from app.modules.realistic_review_ugc import multihat_colorways

    image = Image.new("RGB", (700, 540), "#ece9d8")
    buffer = BytesIO()
    image.save(buffer, "JPEG")
    approved_stock = buffer.getvalue()
    monkeypatch.setattr(multihat_colorways, "_stock_bytes",
                        lambda _settings, _color: approved_stock)
    session, settings = ecommerce_db
    for cap_count in (3, 4):
        for i in range(30):
            pin = str(cap_count * 10000 + i)
            session.add(RrugcCapStackImageModel(
                id=pin, tenant_id="tenant-001", pin_key=pin,
                pin_url=f"https://www.pinterest.com/pin/{pin}/",
                image_url=f"https://i.pinimg.com/736x/{pin}.jpg",
                caps_detected=cap_count, count_status="ready",
                is_cap_stack=True, ai_suspected=False,
            ))
    session.add(RrugcSourcePlanModel(
        id="plan-002", tenant_id="tenant-001",
        root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
        source_file_id="remote-design-two",
        source_relative_path="design-two.png",
        source_name="design-two.png", source_mime_type="image/png",
        source_revision="rev2", created_by_user_id="operator",
    ))
    session.commit()

    service = EcommerceService(session, settings)
    first = service.queue_scene(
        tenant_id="tenant-001", user_id="operator",
        source_plan_id="plan-001", slot="prod_s2_mh")
    assert len(first["steps"]) == 3
    assert all(item["status"] == "queued" for item in first["steps"])
    jobs = [session.get(RrugcEcommerceJobModel, item["job_id"]) for item in first["steps"]]
    attempts = [session.get(ProcessingJobModel, row.processing_job_id) for row in jobs]
    samples = [job.payload_json["multihat_references"] for job in attempts]
    assert len(samples[0]) == 20
    assert samples[0] == samples[1] == samples[2]
    assert sum(ref["caps_count"] == 3 for ref in samples[0]) == 10
    assert sum(ref["caps_count"] == 4 for ref in samples[0]) == 10
    palettes = [attempt.payload_json["multihat_colorways"] for attempt in attempts]
    assert list(map(len, palettes)) == [4, 4, 3]
    assert all(len(set(colors)) == len(colors) for colors in palettes)
    assert len(set(color for colors in palettes for color in colors)) >= 9
    assert all(set(attempt.payload_json["multihat_colorway_hashes"]) == set(colors)
               for attempt, colors in zip(attempts, palettes))

    replay = service.queue_scene(
        tenant_id="tenant-001", user_id="operator",
        source_plan_id="plan-001", slot="prod_s2_mh")
    assert all(item["already_exists"] for item in replay["steps"])
    assert [row.processing_job_id for row in jobs] == [job.id for job in attempts]

    # A failure creates a new attempt, reusing the exact same 20 references.
    jobs[1].status = "failed"
    attempts[1].status = "failed"
    session.commit()
    retry = service.queue_scene(
        tenant_id="tenant-001", user_id="operator",
        source_plan_id="plan-001", slot="prod_s2_mh")
    assert retry["steps"][1]["status"] == "queued"
    retried = session.get(ProcessingJobModel, jobs[1].processing_job_id)
    assert retried.id != attempts[1].id
    assert retried.payload_json["multihat_references"] == samples[0]
    assert retried.payload_json["multihat_colorways"] == palettes[1]
    assert retried.payload_json["multihat_colorway_hashes"] == attempts[1].payload_json["multihat_colorway_hashes"]

    # Another embroidery design gets a new independent random 20-photo set.
    second = service.queue_scene(
        tenant_id="tenant-001", user_id="operator",
        source_plan_id="plan-002", slot="prod_s2_mh")
    second_job = session.get(RrugcEcommerceJobModel, second["steps"][0]["job_id"])
    second_attempt = session.get(ProcessingJobModel, second_job.processing_job_id)
    assert {ref["image_id"] for ref in samples[0]} != {
        ref["image_id"] for ref in second_attempt.payload_json["multihat_references"]
    }
    listed = service.list(tenant_id="tenant-001", source_ids=["plan-001", "plan-002"])
    pinned = next(row for row in listed if row["stage"] == "prod_s2_mh_1"
                  and row["source_plan_id"] == "plan-001")
    assert len(pinned["scene_references"]) == 20
    assert len(pinned["scene_colorways"]) == 4
    assert [item["key"] for item in pinned["scene_colorways"]] == palettes[0]

    # Explicit regeneration preserves the old output but draws new per-cap colors.
    jobs[0].status = "completed"
    jobs[0].output_remote_file_id = "old-output-kept"
    session.commit()
    old_attempt_id = jobs[0].processing_job_id
    regen = service.regenerate(tenant_id="tenant-001", job_id=jobs[0].id)
    assert regen["status"] == "queued"
    assert jobs[0].output_remote_file_id == "old-output-kept"
    assert jobs[0].processing_job_id != old_attempt_id
    new_attempt = session.get(ProcessingJobModel, jobs[0].processing_job_id)
    assert len(new_attempt.payload_json["multihat_colorways"]) == 4
    assert new_attempt.payload_json["multihat_colorways"] != palettes[0]

def test_run_all_with_completed_front_starts_all_four(ecommerce_db):
    session, settings = ecommerce_db
    first = EcommerceService(session, settings).queue(
        tenant_id="tenant-001", user_id="operator", source_plan_id="plan-001",
        stage="prod_s2_ec1")
    front = session.get(RrugcEcommerceJobModel, first["job_id"])
    front.status = "completed"
    front.output_remote_file_id = "front-output"
    session.commit()
    result = EcommerceService(session, settings).queue_all(
        tenant_id="tenant-001", user_id="operator", source_plan_id="plan-001")
    states = {step["stage"]: step["status"] for step in result["steps"]}
    assert states["prod_s2_ec1"] == "completed"
    assert states["prod_s2_ec2"] == "queued"
    assert states["prod_s2_ec3"] == "queued"
    assert states["prod_s2_ec4"] == "queued"


def test_run_all_records_individual_failures_without_blocking_other_slots(ecommerce_db, monkeypatch):
    import app.modules.realistic_review_ugc.ecommerce_pipeline as pipeline
    session, settings = ecommerce_db
    original = pipeline._assigned_skill
    def assigned(session, settings, tenant_id, stage):
        if stage == "prod_s2_ec3":
            raise EcommerceError("missing_skill", "Side90 Skill is missing.")
        return original(session, settings, tenant_id, stage)
    monkeypatch.setattr(pipeline, "_assigned_skill", assigned)
    response = EcommerceService(session, settings).queue_all(
        tenant_id="tenant-001", user_id="operator", source_plan_id="plan-001")
    states = {step["stage"]: step for step in response["steps"]}
    assert states["prod_s2_ec4"]["status"] == "queued"
    assert states["prod_s2_ec1"]["status"] == "queued"
    assert states["prod_s2_ec2"]["status"] == "waiting_for_front"
    assert states["prod_s2_ec3"]["status"] == "blocked"
    assert "Side90" in states["prod_s2_ec3"]["reason"]
    front = next(row for row in session.scalars(select(RrugcEcommerceJobModel))
                 if row.stage == "prod_s2_ec1")
    processing = session.get(ProcessingJobModel, front.processing_job_id)
    assert processing.payload_json["run_all_followups"] == ["prod_s2_ec2"]


def test_run_all_retries_a_failed_dependent_when_front_is_completed(ecommerce_db):
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    first = service.queue(tenant_id="tenant-001", user_id="operator",
                          source_plan_id="plan-001", stage="prod_s2_ec1")
    front = session.get(RrugcEcommerceJobModel, first["job_id"])
    front.status = "completed"
    front.output_remote_file_id = "front-original"
    session.commit()
    angle = service.queue(tenant_id="tenant-001", user_id="operator",
                          source_plan_id="plan-001", stage="prod_s2_ec2")
    existing = session.get(RrugcEcommerceJobModel, angle["job_id"])
    previous_processing = existing.processing_job_id
    session.get(ProcessingJobModel, previous_processing).status = "failed"
    existing.status = "failed"
    session.commit()
    result = service.queue_all(tenant_id="tenant-001", user_id="operator",
                               source_plan_id="plan-001")
    steps = {item["stage"]: item for item in result["steps"]}
    assert steps["prod_s2_ec2"]["status"] == "queued"
    assert steps["prod_s2_ec3"]["status"] == "queued"
    assert existing.retry_count == 1
    assert existing.processing_job_id != previous_processing
    assert existing.front_reference_remote_file_id == "front-original"


def test_run_all_fails_closed_for_other_tenant(ecommerce_db):
    session, settings = ecommerce_db
    with pytest.raises(EcommerceError):
        EcommerceService(session, settings).queue_all(
            tenant_id="wrong-tenant", user_id="operator",
            source_plan_id="plan-001")
    assert len(list(session.scalars(select(RrugcEcommerceJobModel)))) == 0


def test_run_all_disabled_returns_no_new_jobs(ecommerce_db):
    session, settings = ecommerce_db
    settings.CODEX_IMAGE_GENERATION_ENABLED = False
    result = EcommerceService(session, settings).queue_all(
        tenant_id="tenant-001", user_id="operator", source_plan_id="plan-001")
    assert all(step["status"] == "blocked" for step in result["steps"])
    assert not list(session.scalars(select(RrugcEcommerceJobModel)))

def test_other_tenant_cannot_queue_same_plan(ecommerce_db):
    session, settings = ecommerce_db
    with pytest.raises(EcommerceError):
        EcommerceService(session, settings).queue(
            tenant_id="other-tenant", user_id="actor",
            source_plan_id="plan-001", stage="prod_s2_ec1")
