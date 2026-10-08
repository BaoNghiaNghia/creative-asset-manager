from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.realistic_review_ugc.keyword_images import (
    KeywordImageError, KeywordImageService, effective_status, keyword_prompt,
)
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordImageJobModel, RrugcKeywordVolumeModel,
)
from app.providers.ai.codex_image import CodexImageGenRunner, CodexImageRunnerConfig


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    for table in (RrugcKeywordVolumeModel.__table__, RrugcKeywordImageJobModel.__table__,
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


def test_stage1_does_not_generate_unpicked_or_other_tenant(db):
    keyword(db, "unused-a", "tenant-a", picked=False)
    with pytest.raises(KeywordImageError, match="Only Stage 0 used keywords"):
        KeywordImageService(db).queue(tenant_id="tenant-a", user_id="actor",
                                      keyword_id="unused-a")
    with pytest.raises(KeywordImageError, match="Only Stage 0 used keywords"):
        KeywordImageService(db).queue(tenant_id="tenant-b", user_id="actor",
                                      keyword_id="unused-a")
    assert not db.query(RrugcKeywordImageJobModel).count()


def test_failed_job_retry_is_tenant_scoped_and_idempotent(db):
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
    assert effective_status(row, old_processing) == "failed"
    with pytest.raises(KeywordImageError, match="No generation job exists"):
        KeywordImageService(db).retry(tenant_id="tenant-b", keyword_id=kw.id)
    result = KeywordImageService(db).retry(tenant_id="tenant-a", keyword_id=kw.id)
    assert result.retry_count == 1
    assert result.status == "queued"
    assert result.processing_job_id != old_processing.id
    assert db.get(ProcessingJobModel, result.processing_job_id).tenant_id == "tenant-a"
    with pytest.raises(KeywordImageError, match="Only failed generations"):
        KeywordImageService(db).retry(tenant_id="tenant-a", keyword_id=kw.id)


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
    with pytest.raises(KeywordImageError, match="Only failed generations"):
        KeywordImageService(db).retry(tenant_id="tenant-a", keyword_id=kw.id)
    assert db.get(RrugcKeywordImageJobModel, row.id).output_remote_file_id == "output-safe"


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
