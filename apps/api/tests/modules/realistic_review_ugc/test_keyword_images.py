from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.realistic_review_ugc.keyword_images import (
    DEFAULT_SKILL, KeywordImageError, KeywordImageService, effective_status, keyword_prompt,
)
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordImageJobModel, RrugcKeywordVolumeModel,
)
from app.providers.ai.codex_image import CodexImageGenRunner, CodexImageRunnerConfig


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    for table in (RrugcKeywordVolumeModel.__table__, RrugcKeywordImageJobModel.__table__,
                  RrugcImageOutputVersionModel.__table__,
                  ProcessingJobModel.__table__, TenantProcessingPolicyModel.__table__):
        table.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def keyword(db: Session, id: str, tenant: str, *, picked: bool):
    row = RrugcKeywordVolumeModel(
        id=id, tenant_id=tenant, keyword=id + " HATS",
        keyword_normalized=id.lower() + " hats", search_volume=100,
        picked=picked, provider="test",
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    return row


def test_stage1_only_lists_used_keyword_and_is_tenant_scoped(db):
    first = keyword(db, "used-a", "tenant-a", picked=True)
    keyword(db, "unused-a", "tenant-a", picked=False)
    keyword(db, "used-b", "tenant-b", picked=True)
    item = KeywordImageService(db).list_used(tenant_id="tenant-a", page=1, page_size=20)
    assert item["total"] == 1
    assert item["items"][0]["keyword_id"] == first.id
    assert item["items"][0]["status"] == "not_run"
    assert KeywordImageService(db).list_used(tenant_id="tenant-b", page=1, page_size=20)["total"] == 1


def test_stage1_job_timestamps_use_actual_run_and_completion_times(db):
    row = keyword(db, "used-timed", "tenant-a", picked=True)
    started = datetime(2026, 10, 10, 1, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 10, 10, 1, 2, tzinfo=timezone.utc)
    job = RrugcKeywordImageJobModel(
        id="timed-job", tenant_id=row.tenant_id, keyword_id=row.id,
        keyword_text=row.keyword, skill_source="local", skill_name="skill",
        prompt_text="prompt", status="completed", created_by_user_id="actor",
        started_at=started, completed_at=finished,
    )
    db.add(job)
    db.commit()
    item = KeywordImageService(db).list_used(tenant_id="tenant-a", page=1, page_size=20)["items"][0]
    assert item["started_at"].startswith("2026-10-10T01:00:00")
    assert item["finished_at"].startswith("2026-10-10T01:02:00")
    other = KeywordImageService(db).list_used(tenant_id="tenant-b", page=1, page_size=20)
    assert other["items"] == []


def test_stage1_failed_job_has_processing_finish_time_even_without_keyword_completion(db):
    row = keyword(db, "used-failed", "tenant-a", picked=True)
    started = datetime(2026, 10, 10, 1, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 10, 10, 1, 1, tzinfo=timezone.utc)
    processing = ProcessingJobModel(
        id="failed-process", tenant_id="tenant-a", job_type="rrugc_keyword_image_generate",
        entity_type="rrugc_keyword_image_job", entity_id="failed-job",
        idempotency_key="failed-process", payload_json={}, status="failed", max_attempts=1,
        claimed_at=started, completed_at=finished,
    )
    job = RrugcKeywordImageJobModel(
        id="failed-job", tenant_id=row.tenant_id, keyword_id=row.id,
        keyword_text=row.keyword, skill_source="local", skill_name="skill",
        prompt_text="prompt", status="failed", created_by_user_id="actor",
        processing_job_id=processing.id,
    )
    db.add_all([processing, job])
    db.commit()
    item = KeywordImageService(db).list_used(tenant_id="tenant-a", page=1, page_size=20)["items"][0]
    assert item["status"] == "failed"
    assert item["started_at"].startswith("2026-10-10T01:00:00")
    assert item["finished_at"].startswith("2026-10-10T01:01:00")


def test_stage1_saved_output_count_is_scoped_and_available_on_failed_jobs(db):
    first = keyword(db, "used-a", "tenant-a", picked=True)
    kw_other = keyword(db, "used-b", "tenant-b", picked=True)
    db.add_all([
        RrugcKeywordImageJobModel(
            id="job-one", tenant_id=first.tenant_id, keyword_id=first.id,
            keyword_text=first.keyword, skill_source="local", skill_name="skill",
            prompt_text="prompt", status="failed", created_by_user_id="actor",
            output_remote_file_id="file-one",
        ),
        RrugcKeywordImageJobModel(
            id="job-other", tenant_id=kw_other.tenant_id, keyword_id=kw_other.id,
            keyword_text=kw_other.keyword, skill_source="local", skill_name="skill",
            prompt_text="prompt", status="completed", created_by_user_id="actor",
        ),
        RrugcImageOutputVersionModel(
            id="version-a", tenant_id="tenant-a", stage="stage1", job_id="job-one",
            version=1, remote_file_id="file-one",
        ),
        RrugcImageOutputVersionModel(
            id="version-b", tenant_id="tenant-a", stage="stage1", job_id="job-one",
            version=2, remote_file_id="file-two",
        ),
        RrugcImageOutputVersionModel(
            id="version-other", tenant_id="tenant-b", stage="stage1", job_id="job-other",
            version=1, remote_file_id="other-tenant",
        ),
    ])
    db.commit()
    page = KeywordImageService(db).list_used(tenant_id="tenant-a", page=1, page_size=20)
    assert page["items"][0]["status"] == "failed"
    assert page["items"][0]["saved_output_count"] == 2
    assert page["items"][0]["output_url"].endswith("/jobs/job-one/output")
    assert KeywordImageService(db).list_used(tenant_id="tenant-b", page=1, page_size=20)["items"][0]["saved_output_count"] == 1


def test_stage1_does_not_generate_unpicked_or_other_tenant(db):
    keyword(db, "unused-a", "tenant-a", picked=False)
    with pytest.raises(KeywordImageError, match="Only Stage 0 used keywords"):
        KeywordImageService(db).queue(tenant_id="tenant-a", user_id="actor",
                                      keyword_id="unused-a")
    with pytest.raises(KeywordImageError, match="Only Stage 0 used keywords"):
        KeywordImageService(db).queue(tenant_id="tenant-b", user_id="actor",
                                      keyword_id="unused-a")
    assert not db.query(RrugcKeywordImageJobModel).count()


def test_failed_stage1_job_never_creates_a_second_run(db):
    kw = keyword(db, "used-a", "tenant-a", picked=True)
    row = RrugcKeywordImageJobModel(
        id="job-a", tenant_id=kw.tenant_id, keyword_id=kw.id,
        keyword_text=kw.keyword, skill_source="local", skill_name="skill",
        prompt_text=keyword_prompt(kw.keyword), status="failed", retry_count=0,
        created_by_user_id="actor",
    )
    old_processing = ProcessingJobModel(
        id="processing-old", tenant_id="tenant-a", job_type="rrugc_keyword_image_generate",
        entity_type="rrugc_keyword_image_job", entity_id="job-a",
        idempotency_key="test-old", payload_json={"keyword_image_job_id": "job-a"},
        status="failed", max_attempts=3,
    )
    row.processing_job_id = old_processing.id
    db.add_all([old_processing, row])
    db.commit()
    with pytest.raises(KeywordImageError, match="No generation job exists"):
        KeywordImageService(db).retry(tenant_id="tenant-b", keyword_id=kw.id)
    with pytest.raises(KeywordImageError, match="one attempt per job"):
        KeywordImageService(db).retry(tenant_id="tenant-a", keyword_id=kw.id)
    assert row.processing_job_id == old_processing.id
    assert row.retry_count == 0
    assert db.query(ProcessingJobModel).count() == 1


def test_stage1_enqueue_has_one_worker_attempt(db):
    kw = keyword(db, "used-new", "tenant-a", picked=True)
    row = RrugcKeywordImageJobModel(
        id="new-job", tenant_id=kw.tenant_id, keyword_id=kw.id,
        keyword_text=kw.keyword, skill_source="local", skill_name="skill",
        prompt_text=keyword_prompt(kw.keyword), status="queued",
        created_by_user_id="actor",
    )
    db.add(row)
    db.flush()
    KeywordImageService(db)._enqueue(row)
    db.commit()
    assert db.get(ProcessingJobModel, row.processing_job_id).max_attempts == 1


def test_completed_job_remains_untouched_by_manual_retry(db):
    kw = keyword(db, "used-a", "tenant-a", picked=True)
    row = RrugcKeywordImageJobModel(
        id="job-complete", tenant_id="tenant-a", keyword_id=kw.id,
        keyword_text=kw.keyword, skill_source="local", skill_name="skill",
        prompt_text="prompt", status="completed", output_remote_file_id="output-safe",
        created_by_user_id="actor",
    )
    db.add(row)
    db.commit()
    with pytest.raises(KeywordImageError, match="one attempt per job"):
        KeywordImageService(db).retry(tenant_id="tenant-a", keyword_id=kw.id)
    assert db.get(RrugcKeywordImageJobModel, row.id).output_remote_file_id == "output-safe"


def test_stage1_skill_compatibility_only_accepts_six_final_designs(monkeypatch):
    from types import SimpleNamespace

    from app.modules.realistic_review_ugc import stage_skill_settings

    manifests = {
        "six-designs": SimpleNamespace(workflows=("image_studio", "keyword_artwork", "keyword_six_designs"), required_reference_roles=()),
        "keyword": SimpleNamespace(workflows=("image_studio", "keyword_artwork"), required_reference_roles=()),
        "redesign": SimpleNamespace(workflows=("image_studio",), required_reference_roles=()),
    }
    monkeypatch.setattr(
        stage_skill_settings, "load_codex_skill_manifest",
        lambda _home, name: manifests.get(name),
    )
    fake_settings = SimpleNamespace(CODEX_IMAGE_HOME="/unused")
    assert stage_skill_settings.keyword_skill_compatible("six-designs", settings=fake_settings)
    assert not stage_skill_settings.keyword_skill_compatible("keyword", settings=fake_settings)
    assert not stage_skill_settings.keyword_skill_compatible("redesign", settings=fake_settings)
    assert not stage_skill_settings.keyword_skill_compatible("missing", settings=fake_settings)


def test_stage1_ships_dedicated_zero_reference_keyword_skill():
    from pathlib import Path
    import json

    root = Path(__file__).resolve().parents[5]
    skill_dir = root / "apps" / "api" / "managed_skills" / DEFAULT_SKILL
    manifest = json.loads((skill_dir / "manifest.json").read_text(encoding="utf-8"))
    instructions = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert manifest["skill_name"] == DEFAULT_SKILL
    assert "keyword_artwork" in manifest["workflows"]
    assert manifest["required_reference_roles"] == []
    assert "keyword_six_designs" in manifest["workflows"]
    assert "design_06.png" in instructions
    assert "640" in instructions


def test_codex_keyword_prompt_does_not_require_an_input_image():
    runner = CodexImageGenRunner(CodexImageRunnerConfig(
        binary="codex", codex_home="/tmp/not-used", staging_root="/tmp/not-used",
        skill_name="keyword-image-skill", timeout_seconds=600,
    ))
    instruction = runner._prompt(person_name=None, references=[], user_prompt=keyword_prompt("HOUSTON ASTROS"))
    assert "HOUSTON ASTROS" in instruction
    assert "Create a new original image" in instruction
    assert "Edit target:" not in instruction
    assert "$keyword-image-skill" in instruction

def test_redesign_keyword_prompt_creates_ten_concept_board():
    prompt = keyword_prompt("BEACH PLEASE", skill_name="redesign-8869-v3")
    assert "10 materially distinct" in prompt
    assert "ONE on-cap" in prompt
    assert "Valucap 8869" in prompt
    assert "BEACH PLEASE" in prompt
    assert "do not generate 13 colorway images" in prompt
    assert "10 materially distinct" not in keyword_prompt("BEACH PLEASE")

def test_manual_keyword_creates_distinct_stage1_keyword_without_changing_stage0(db, monkeypatch):
    service = KeywordImageService(db)
    invoked = []

    def fake_queue(**kwargs):
        invoked.append(kwargs)
        return type("Queued", (), {"keyword_id": kwargs["keyword_id"], "id": "job-123"})(), True

    monkeypatch.setattr(service, "queue", fake_queue)
    queued, created = service.queue_manual(
        tenant_id="tenant-a", user_id="actor", text="  BEACH   PLEASE  ",
        skill_source="local", skill_name="redesign-8869-v3",
    )
    assert created is True and queued.id == "job-123"
    row = db.get(RrugcKeywordVolumeModel, queued.keyword_id)
    assert row.keyword == "BEACH PLEASE"
    assert row.picked is True and row.provider == "manual_stage1"
    assert row.search_volume == 0
    assert row.keyword_normalized.startswith("manual-stage1:")
    assert invoked[0]["skill_name"] == "redesign-8869-v3"
    assert invoked[0]["tenant_id"] == "tenant-a"


def test_manual_keyword_rejects_empty_and_overlong_inputs(db):
    service = KeywordImageService(db)
    for invalid in (" ", " " * 4, "A" * 151, "A" + chr(10) + "B"):
        with pytest.raises(KeywordImageError) as err:
            service.queue_manual(tenant_id="tenant-a", user_id="actor", text=invalid)
        assert err.value.status_code == 422
    assert db.query(RrugcKeywordVolumeModel).count() == 0
