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
    RrugcSourcePlanModel,
)
from app.providers.ai.codex_image import CodexImageGenRunner, CodexImageRunnerConfig


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    for table in (RrugcSourcePlanModel.__table__, RrugcKeywordVolumeModel.__table__, RrugcKeywordImageJobModel.__table__,
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





def test_stage1_recovery_repins_only_same_enabled_six_design_skill(db, monkeypatch):
    """Old jobs can follow a same-Skill version update, never a different Skill."""
    from types import SimpleNamespace
    from app.operations import stage1_recovery
    from app.modules.realistic_review_ugc.stage2_skills import Stage2SkillRegistryError

    kw = keyword(db, "upgraded-skill", "tenant-a", picked=True)
    job = RrugcKeywordImageJobModel(
        id="upgraded-job", tenant_id=kw.tenant_id, keyword_id=kw.id,
        keyword_text=kw.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
        skill_version="1.1.0", skill_bundle_sha256="old-bundle",
        prompt_text="six designs", status="failed", created_by_user_id="actor",
    )
    db.add(job)
    db.commit()
    settings = SimpleNamespace(CODEX_IMAGE_HOME="/fake/home")
    resolved = SimpleNamespace(
        source="local", skill_id=None, skill_name=DEFAULT_SKILL, skill_version="1.2.0"
    )
    events = []
    monkeypatch.setattr(stage1_recovery, "resolve_stage2_skill", lambda **kwargs: resolved)
    monkeypatch.setattr(stage1_recovery, "ensure_skill_registry", lambda *args, **kwargs: events.append("registry"))
    monkeypatch.setattr(stage1_recovery, "assert_skill_enabled", lambda *args, **kwargs: events.append("enabled"))
    monkeypatch.setattr(stage1_recovery, "load_codex_skill_manifest", lambda *args: SimpleNamespace(workflows=("keyword_six_designs",)))
    monkeypatch.setattr(stage1_recovery, "installed_stage2_skill_sha256", lambda *args, **kwargs: "new-bundle")
    assert stage1_recovery.refresh_job_skill(db, job, settings)
    assert job.skill_name == DEFAULT_SKILL
    assert job.skill_version == "1.2.0"
    assert job.skill_bundle_sha256 == "new-bundle"
    assert events == ["registry", "enabled"]

    # Cannot substitute a different Skill or a disabled Skill in background.
    old_version = job.skill_version
    monkeypatch.setattr(
        stage1_recovery, "resolve_stage2_skill",
        lambda **kwargs: SimpleNamespace(
            source="local", skill_id=None, skill_name="another-skill", skill_version="2.0.0"
        )
    )
    assert stage1_recovery.refresh_job_skill(db, job, settings) is False
    assert job.skill_version == old_version
    monkeypatch.setattr(stage1_recovery, "resolve_stage2_skill", lambda **kwargs: resolved)
    def disabled(*_args, **_kwargs):
        raise Stage2SkillRegistryError("stage2_skill_disabled", "Disabled")
    monkeypatch.setattr(stage1_recovery, "assert_skill_enabled", disabled)
    assert stage1_recovery.refresh_job_skill(db, job, settings) is False
    assert job.skill_version == old_version


def test_stage1_scheduled_recovery_only_requeues_bounded_safe_failures(db, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from app.operations import stage1_recovery
    monkeypatch.setattr(stage1_recovery, "SessionLocal",
                        sessionmaker(bind=db.bind, expire_on_commit=False))
    monkeypatch.setattr(stage1_recovery, "refresh_job_skill", lambda *_args: True)
    kw = keyword(db, "retry-old", "tenant-a", picked=True)
    kw2 = keyword(db, "do-not-retry", "tenant-a", picked=True)
    db.add_all([
        RrugcKeywordImageJobModel(
            id="safe-failure", tenant_id="tenant-a", keyword_id=kw.id,
            keyword_text=kw.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
            prompt_text="prompt", status="failed", created_by_user_id="actor",
            last_error_code="stage1_imagegen_not_invoked",
        ),
        RrugcKeywordImageJobModel(
            id="skill-error", tenant_id="tenant-a", keyword_id=kw2.id,
            keyword_text=kw2.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
            prompt_text="prompt", status="failed", created_by_user_id="actor",
            last_error_code="stage1_imagegen_unavailable",
        ),
    ])
    db.commit()
    dry = stage1_recovery.recover_failed(limit=5, dry_run=True)
    assert dry == {"eligible": 1, "queued": 0, "upload_resumed": 0, "skipped": 0}
    result = stage1_recovery.recover_failed(limit=5)
    assert result["queued"] == 1
    db.expire_all()
    recovered = db.get(RrugcKeywordImageJobModel, "safe-failure")
    assert recovered.status == "queued"
    assert recovered.retry_count == 1
    assert db.get(ProcessingJobModel, recovered.processing_job_id).status == "pending"
    assert db.get(RrugcKeywordImageJobModel, "skill-error").status == "failed"
    assert stage1_recovery.recover_failed(limit=5)["queued"] == 0


def test_stage1_audit_counts_only_active_six_png_generation(db, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from app.operations import stage1_recovery

    monkeypatch.setattr(stage1_recovery, "SessionLocal",
                        sessionmaker(bind=db.bind, expire_on_commit=False))
    full = keyword(db, "audit-full", "tenant-a", picked=True)
    old = keyword(db, "audit-old", "tenant-a", picked=True)
    failed = keyword(db, "audit-failed", "tenant-a", picked=True)
    db.add_all([
        RrugcKeywordImageJobModel(
            id="audit-full-job", tenant_id="tenant-a", keyword_id=full.id,
            keyword_text=full.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
            prompt_text="prompt", status="completed", created_by_user_id="actor",
            output_remote_file_id="full-1",
        ),
        RrugcKeywordImageJobModel(
            id="audit-old-job", tenant_id="tenant-a", keyword_id=old.id,
            keyword_text=old.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
            prompt_text="prompt", status="completed", created_by_user_id="actor",
            output_remote_file_id="new-1",
        ),
        RrugcKeywordImageJobModel(
            id="audit-failed-job", tenant_id="tenant-a", keyword_id=failed.id,
            keyword_text=failed.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
            prompt_text="prompt", status="failed", retry_count=3,
            last_error_code="stage1_six_outputs_invalid", created_by_user_id="actor",
        ),
    ])
    versions = []
    for job_id, processing_id, prefix, first_version, size in [
        ("audit-full-job", "p1", "full", 1, 6),
        ("audit-old-job", "previous", "previous", 1, 6),
        ("audit-old-job", "current", "new", 7, 5),
    ]:
        for index in range(1, size + 1):
            versions.append(RrugcImageOutputVersionModel(
                id=f"{job_id}-{processing_id}-{index}", tenant_id="tenant-a",
                stage="stage1", job_id=job_id, version=first_version + index - 1,
                remote_file_id=f"{prefix}-{index}",
                processing_job_id=processing_id,
                output_name=f"output/final/design_{index:02}.png",
                content_type="image/png",
            ))
    db.add_all(versions)
    db.commit()

    snapshot = stage1_recovery.audit_stage1()
    assert snapshot["statuses"] == {"completed": 2, "failed": 1}
    assert snapshot["completed_verified_six"] == 1
    assert snapshot["completed_unverified"] == 1
    assert snapshot["failed_codes"] == {"stage1_six_outputs_invalid": 1}
    assert snapshot["failed_retry_budget_exhausted"] == 1


def test_stage1_invalid_six_png_set_is_not_requeued_without_validation(db, tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from app.operations import stage1_recovery
    from types import SimpleNamespace
    from PIL import Image

    monkeypatch.setattr(stage1_recovery, "SessionLocal",
                        sessionmaker(bind=db.bind, expire_on_commit=False))
    monkeypatch.setattr(stage1_recovery, "refresh_job_skill", lambda *_args: True)
    kw = keyword(db, "audit-extra", "tenant-a", picked=True)
    db.add(RrugcKeywordImageJobModel(
        id="extra-final-job", tenant_id="tenant-a", keyword_id=kw.id,
        keyword_text=kw.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
        prompt_text="prompt", status="failed", created_by_user_id="actor",
        last_error_code="stage1_six_outputs_invalid",
    ))
    db.commit()
    workspace = tmp_path / "codex" / "extra-final-job" / "output" / "final"
    workspace.mkdir(parents=True)
    for i in range(1, 8):
        Image.new("RGBA", (800, 800), (i * 25, 40, 70)).save(
            workspace / f"design_{i:02}.png"
        )
    monkeypatch.setattr(stage1_recovery, "get_settings",
                        lambda: SimpleNamespace(IMAGE_GENERATION_STAGING_ROOT=str(tmp_path)))
    result = stage1_recovery.recover_failed(limit=5)
    assert result["queued"] == 0 and result["skipped"] == 1
    assert db.get(RrugcKeywordImageJobModel, "extra-final-job").status == "failed"
    assert all((workspace / f"design_{i:02}.png").is_file() for i in range(1, 8))


def test_stage1_resume_failure_logs_safe_error_code_without_retrying_generation(db, monkeypatch, caplog):
    from sqlalchemy.orm import sessionmaker
    from app.operations import stage1_recovery
    from app.modules.realistic_review_ugc.keyword_images import KeywordImageError

    monkeypatch.setattr(stage1_recovery, "SessionLocal",
                        sessionmaker(bind=db.bind, expire_on_commit=False))
    kw = keyword(db, "missing-checkpoint", "tenant-a", picked=True)
    processing = ProcessingJobModel(
        id="upload-before", tenant_id="tenant-a", job_type="rrugc_keyword_image_generate",
        entity_type="rrugc_keyword_image_job", entity_id="upload-failed",
        idempotency_key="upload-before", payload_json={"keyword_image_job_id": "upload-failed"},
        status="failed", max_attempts=1,
    )
    db.add_all([processing, RrugcKeywordImageJobModel(
        id="upload-failed", tenant_id="tenant-a", keyword_id=kw.id,
        keyword_text=kw.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
        prompt_text="prompt", status="failed", created_by_user_id="actor",
        processing_job_id=processing.id,
        last_error_code="managed_storage_temporarily_unavailable",
    )])
    db.commit()

    def unavailable(_self, *, tenant_id, keyword_id):
        assert tenant_id == "tenant-a" and keyword_id == kw.id
        raise KeywordImageError("keyword_image_saved_finals_missing", "secret provider response")

    monkeypatch.setattr(stage1_recovery.KeywordImageService, "resume_upload", unavailable)
    result = stage1_recovery.recover_failed(limit=5)
    assert result == {"eligible": 1, "queued": 0, "upload_resumed": 0, "skipped": 1}
    assert "code=keyword_image_saved_finals_missing" in caplog.text
    assert "secret provider response" not in caplog.text
    assert db.get(RrugcKeywordImageJobModel, "upload-failed").status == "failed"


def test_stage1_missing_upload_finals_restarts_only_when_no_other_artwork(db, tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from types import SimpleNamespace
    from PIL import Image
    from app.operations import stage1_recovery
    from app.modules.realistic_review_ugc.keyword_images import KeywordImageError

    monkeypatch.setattr(stage1_recovery, "SessionLocal",
                        sessionmaker(bind=db.bind, expire_on_commit=False))
    monkeypatch.setattr(stage1_recovery, "get_settings",
                        lambda: SimpleNamespace(IMAGE_GENERATION_STAGING_ROOT=str(tmp_path)))
    monkeypatch.setattr(stage1_recovery, "refresh_job_skill", lambda *_args: True)
    kw = keyword(db, "rebuild-failed-uploads", "tenant-a", picked=True)
    old = ProcessingJobModel(
        id="lost-before", tenant_id="tenant-a", job_type="rrugc_keyword_image_generate",
        entity_type="rrugc_keyword_image_job", entity_id="lost-image-job",
        idempotency_key="lost-before", payload_json={"keyword_image_job_id": "lost-image-job"},
        status="failed", max_attempts=1,
    )
    db.add_all([old, RrugcKeywordImageJobModel(
        id="lost-image-job", tenant_id="tenant-a", keyword_id=kw.id,
        keyword_text=kw.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
        prompt_text="prompt", status="failed", created_by_user_id="actor",
        output_remote_file_id="published-old-partial",
        processing_job_id=old.id,
        last_error_code="managed_storage_temporarily_unavailable",
    )])
    db.commit()

    def missing(_self, *, tenant_id, keyword_id):
        raise KeywordImageError("keyword_image_saved_finals_missing", "Missing original files")
    monkeypatch.setattr(stage1_recovery.KeywordImageService, "resume_upload", missing)

    # A stray image outside output/final may be a user's last surviving
    # artwork. Never delete it just to repeat an imagegen call.
    stray = tmp_path / "codex" / "lost-image-job" / "output" / "drafts"
    stray.mkdir(parents=True)
    Image.new("RGBA", (640, 640), (1, 2, 3)).save(stray / "possible_master.png")
    blocked = stage1_recovery.recover_failed(limit=5)
    assert blocked["queued"] == 0 and blocked["skipped"] == 1
    assert (stray / "possible_master.png").is_file()

    (stray / "possible_master.png").unlink()
    result = stage1_recovery.recover_failed(limit=5)
    assert result["queued"] == 1 and result["skipped"] == 0
    db.expire_all()
    new = db.get(RrugcKeywordImageJobModel, "lost-image-job")
    assert new.status == "queued" and new.retry_count == 1
    assert new.output_remote_file_id == "published-old-partial"
    assert new.processing_job_id != old.id
    assert db.get(ProcessingJobModel, old.id).status == "failed"
    queued = db.get(ProcessingJobModel, new.processing_job_id)
    assert queued.status == "pending"
    assert queued.payload_json["replace_outputs"] is True


def test_stage1_missing_artwork_safety_guard_preserves_partial_and_rejects_unsafe(tmp_path):
    from PIL import Image
    from app.operations.stage1_recovery import resumable_or_empty_stage1_workspace

    workspace = tmp_path / "safe-job"
    final = workspace / "output" / "final"
    final.mkdir(parents=True)
    assert resumable_or_empty_stage1_workspace(workspace)
    Image.new("RGBA", (800, 800), (40, 80, 100)).save(final / "design_01.png")
    assert resumable_or_empty_stage1_workspace(workspace)
    Image.new("RGBA", (800, 800), (40, 80, 100)).save(final / "design_02.png")
    assert not resumable_or_empty_stage1_workspace(workspace)  # duplicate/hash collision
    (final / "design_02.png").unlink()
    (final / "design_01.png").unlink()
    external = tmp_path / "outside.png"
    Image.new("RGBA", (800, 800), (50, 90, 110)).save(external)
    (final / "design_01.png").symlink_to(external)
    assert not resumable_or_empty_stage1_workspace(workspace)


def test_stage1_zero_output_auto_recovery_is_bounded_durable_and_tenant_safe(db):
    from datetime import timedelta
    from types import SimpleNamespace
    from sqlalchemy.orm import Session
    from app.modules.realistic_review_ugc.keyword_images import (
        KeywordImageGenerateHandler, STAGE1_AUTO_RETRY_DELAYS_SECONDS,
    )
    from app.providers.ai.codex_image import CodexImageProviderError

    key = keyword(db, "recov-key", "tenant-a", picked=True)
    row = RrugcKeywordImageJobModel(
        id="stage1-recov", tenant_id="tenant-a", keyword_id=key.id,
        keyword_text=key.keyword, skill_source="local", skill_name="gatorhats-stage1-six-designs",
        prompt_text="prompt", status="running", created_by_user_id="actor",
    )
    db.add(row)
    db.commit()
    messages = []
    def factory():
        return Session(db.bind)
    context = SimpleNamespace(
        job=SimpleNamespace(entity_id=row.id, tenant_id=row.tenant_id),
        dependencies=SimpleNamespace(session_factory=factory), is_cancelled=False,
        logger=SimpleNamespace(warning=lambda message, **kw: messages.append(message)),
    )
    handler = KeywordImageGenerateHandler()
    for index, delay in enumerate(STAGE1_AUTO_RETRY_DELAYS_SECONDS, 1):
        before = datetime.now(timezone.utc)
        outcome = handler._defer_safe_zero_output(
            context, CodexImageProviderError("stage1_imagegen_not_invoked", "No tool call")
        )
        assert outcome is not None and outcome.reason_code == "stage1_imagegen_not_invoked"
        assert before + timedelta(seconds=delay - 1) <= outcome.retry_at <= datetime.now(timezone.utc) + timedelta(seconds=delay + 1)
        db.expire_all()
        updated = db.get(RrugcKeywordImageJobModel, row.id)
        assert updated.retry_count == index
        assert updated.status == "queued"
        assert updated.last_error_code == "stage1_imagegen_not_invoked"
        assert "Auto-recovery" in updated.last_error_message
    assert handler._defer_safe_zero_output(
        context, CodexImageProviderError("stage1_imagegen_not_invoked", "No tool call")
    ) is None
    db.expire_all()
    assert db.get(RrugcKeywordImageJobModel, row.id).retry_count == 3
    assert len(messages) == 3

    # Failed imagegen invocation, incomplete 2/6 output and config errors
    # must not be blindly retried while Stage 1 lacks per-design checkpoints.
    for code in ("stage1_imagegen_no_output", "stage1_six_outputs_invalid",
                 "stage1_imagegen_unavailable", "codex_image_timeout"):
        assert handler._defer_safe_zero_output(context, CodexImageProviderError(code, "Failure")) is None

    # A different tenant cannot schedule retries for this row.
    other_context = SimpleNamespace(
        job=SimpleNamespace(entity_id=row.id, tenant_id="tenant-b"),
        dependencies=context.dependencies, is_cancelled=False, logger=context.logger,
    )
    assert handler._defer_safe_zero_output(
        other_context, CodexImageProviderError("stage1_imagegen_limited", "Quota")
    ) is None


def test_stage1_auto_recovery_skips_existing_outputs_and_cancellation(db):
    from types import SimpleNamespace
    from sqlalchemy.orm import Session
    from app.modules.realistic_review_ugc.keyword_images import KeywordImageGenerateHandler
    from app.providers.ai.codex_image import CodexImageProviderError
    kw = keyword(db, "has-out", "tenant-a", picked=True)
    row = RrugcKeywordImageJobModel(
        id="stage1-preserved", tenant_id="tenant-a", keyword_id=kw.id, keyword_text=kw.keyword,
        skill_source="local", skill_name="gatorhats-stage1-six-designs", prompt_text="",
        status="running", output_remote_file_id="keep-this-drive-image", created_by_user_id="actor",
    )
    db.add(row)
    db.commit()
    context = SimpleNamespace(
        job=SimpleNamespace(entity_id=row.id, tenant_id=row.tenant_id),
        dependencies=SimpleNamespace(session_factory=lambda: Session(db.bind)),
        is_cancelled=False, logger=SimpleNamespace(warning=lambda *args, **kw: None),
    )
    handler = KeywordImageGenerateHandler()
    error = CodexImageProviderError("stage1_imagegen_not_invoked", "No tool")
    assert handler._defer_safe_zero_output(context, error) is None
    row.output_remote_file_id = None
    db.commit()
    context.is_cancelled = True
    assert handler._defer_safe_zero_output(context, error) is None
    db.expire_all()
    assert db.get(RrugcKeywordImageJobModel, row.id).retry_count == 0


def test_stage1_only_lists_used_keyword_and_is_tenant_scoped(db):
    first = keyword(db, "used-a", "tenant-a", picked=True)
    keyword(db, "unused-a", "tenant-a", picked=False)
    keyword(db, "used-b", "tenant-b", picked=True)
    item = KeywordImageService(db).list_used(tenant_id="tenant-a", page=1, page_size=20)
    assert item["total"] == 1
    assert item["items"][0]["keyword_id"] == first.id
    assert item["items"][0]["status"] == "not_run"
    assert KeywordImageService(db).list_used(tenant_id="tenant-b", page=1, page_size=20)["total"] == 1


def test_stage1_sort_applies_across_pages_and_keeps_missing_values_last(db):
    first = keyword(db, "alpha", "tenant-a", picked=True)
    second = keyword(db, "zebra", "tenant-a", picked=True)
    third = keyword(db, "middle", "tenant-a", picked=True)
    other = keyword(db, "other", "tenant-b", picked=True)
    first.search_volume = 300
    second.search_volume = 50
    third.search_volume = 200
    other.search_volume = 9999
    first.provider_raw_json = {"trend": [{"period": "2026-08", "volume": 120},
                                         {"period": "2026-09", "volume": 300}],
                               "three_month_change_pct": 20, "yoy_change_pct": 3.5}
    started = datetime(2026, 10, 10, 1, 0, tzinfo=timezone.utc)
    db.add_all([
        RrugcKeywordImageJobModel(
            id="job-alpha", tenant_id="tenant-a", keyword_id=first.id,
            keyword_text=first.keyword, skill_source="local", skill_name="skill",
            prompt_text="prompt", status="completed", created_by_user_id="actor",
            started_at=started, completed_at=datetime(2026, 10, 10, 1, 1, tzinfo=timezone.utc),
        ),
        RrugcKeywordImageJobModel(
            id="job-zebra", tenant_id="tenant-a", keyword_id=second.id,
            keyword_text=second.keyword, skill_source="local", skill_name="skill",
            prompt_text="prompt", status="completed", created_by_user_id="actor",
            started_at=started, completed_at=datetime(2026, 10, 10, 1, 5, tzinfo=timezone.utc),
        ),
    ])
    db.commit()
    service = KeywordImageService(db)
    def ordered(sort_by, sort_dir):
        return [
            row["keyword_id"] for row in service.list_used(
                tenant_id="tenant-a", page=1, page_size=1,
                sort_by=sort_by, sort_dir=sort_dir)["items"]
        ] + [
            row["keyword_id"] for page_num in (2, 3) for row in service.list_used(
                tenant_id="tenant-a", page=page_num, page_size=1,
                sort_by=sort_by, sort_dir=sort_dir)["items"]
        ]
    assert ordered("keyword", "asc") == ["alpha", "middle", "zebra"]
    assert ordered("search_volume", "desc") == ["alpha", "middle", "zebra"]
    assert ordered("duration", "desc") == ["zebra", "alpha", "middle"]
    assert ordered("finished_at", "asc") == ["alpha", "zebra", "middle"]
    row = service.list_used(tenant_id="tenant-a", page=1, page_size=1,
                            sort_by="keyword", sort_dir="asc")["items"][0]
    assert row["trend"] == [{"period": "2026-08", "volume": 120}, {"period": "2026-09", "volume": 300}]
    assert row["three_month_change_pct"] == 20.0
    assert service.list_used(tenant_id="tenant-b", page=1, page_size=10)["total"] == 1
    with pytest.raises(ValueError, match="Unsupported Stage 1 ordering"):
        service.list_used(tenant_id="tenant-a", page=1, page_size=10, sort_by="arbitrary_sql")


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


def test_failed_stage1_retry_queues_one_replacement_run(db):
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
    renewed = KeywordImageService(db).retry(tenant_id="tenant-a", keyword_id=kw.id)
    assert renewed.processing_job_id != old_processing.id
    assert renewed.retry_count == 0
    assert db.get(ProcessingJobModel, renewed.processing_job_id).payload_json["replace_outputs"] is True
    assert db.get(ProcessingJobModel, old_processing.id).status == "failed"
    assert db.query(ProcessingJobModel).count() == 2


def test_failed_drive_job_can_resume_upload_only_from_preserved_finals(db, tmp_path):
    from PIL import Image

    kw = keyword(db, "recover-kw", "tenant-a", picked=True)
    original = ProcessingJobModel(
        id="original-run", tenant_id="tenant-a", job_type="rrugc_keyword_image_generate",
        entity_type="rrugc_keyword_image_job", entity_id="recover-job",
        idempotency_key="original-run", payload_json={"keyword_image_job_id": "recover-job"},
        status="failed", max_attempts=1,
    )
    job = RrugcKeywordImageJobModel(
        id="recover-job", tenant_id="tenant-a", keyword_id=kw.id,
        keyword_text=kw.keyword, skill_source="local",
        skill_name="gatorhats-stage1-six-designs",
        prompt_text="prompt", status="failed", created_by_user_id="actor",
        last_error_code="managed_storage_temporarily_unavailable",
        processing_job_id=original.id,
    )
    db.add_all([original, job])
    db.commit()
    root = tmp_path / "codex" / "recover-job" / "output" / "final"
    root.mkdir(parents=True)
    for index in range(1, 7):
        Image.new("RGB", (800, 800), (index * 30, 40, 60)).save(root / f"design_{index:02}.png")
    settings = get_settings().model_copy(update={"IMAGE_GENERATION_STAGING_ROOT": str(tmp_path)})
    service = KeywordImageService(db, settings)
    item = service.list_used(tenant_id="tenant-a", page=1, page_size=20)["items"][0]
    assert item["upload_recovery_available"] is True
    service.resume_upload(tenant_id="tenant-a", keyword_id=kw.id)
    assert job.processing_job_id != original.id
    resumed = db.get(ProcessingJobModel, job.processing_job_id)
    assert resumed.payload_json["upload_only"] is True
    assert resumed.payload_json["generation_processing_id"] == original.id
    assert resumed.max_attempts == 1
    assert job.status == "queued"
    assert db.query(ProcessingJobModel).count() == 2
    # A second upload-only failure must keep the FIRST generation identity.
    resumed.status = "failed"
    job.status = "failed"
    job.last_error_code = "managed_storage_temporarily_unavailable"
    db.commit()
    service.resume_upload(tenant_id="tenant-a", keyword_id=kw.id)
    second_resume = db.get(ProcessingJobModel, job.processing_job_id)
    assert second_resume.payload_json["generation_processing_id"] == original.id
    assert second_resume.payload_json["upload_only"] is True
    assert db.query(ProcessingJobModel).count() == 3


def test_old_drive_failure_without_local_finals_never_restarts_generation(db, tmp_path):
    kw = keyword(db, "missing-finals", "tenant-a", picked=True)
    original = ProcessingJobModel(
        id="missing-original", tenant_id="tenant-a", job_type="rrugc_keyword_image_generate",
        entity_type="rrugc_keyword_image_job", entity_id="missing-job",
        idempotency_key="missing-original", payload_json={"keyword_image_job_id": "missing-job"},
        status="failed", max_attempts=1,
    )
    job = RrugcKeywordImageJobModel(
        id="missing-job", tenant_id="tenant-a", keyword_id=kw.id,
        keyword_text=kw.keyword, skill_source="local", skill_name="gatorhats-stage1-six-designs",
        prompt_text="prompt", status="failed", created_by_user_id="actor",
        last_error_code="managed_storage_temporarily_unavailable",
        processing_job_id=original.id,
    )
    db.add_all([original, job])
    db.commit()
    settings = get_settings().model_copy(update={"IMAGE_GENERATION_STAGING_ROOT": str(tmp_path)})
    service = KeywordImageService(db, settings)
    assert service.list_used(tenant_id="tenant-a", page=1, page_size=20)["items"][0]["upload_recovery_available"] is False
    with pytest.raises(KeywordImageError, match="unavailable"):
        service.resume_upload(tenant_id="tenant-a", keyword_id=kw.id)
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


def test_completed_job_retry_is_explicit_replacement_worker_cleanup(db):
    kw = keyword(db, "used-a", "tenant-a", picked=True)
    row = RrugcKeywordImageJobModel(
        id="job-complete", tenant_id="tenant-a", keyword_id=kw.id,
        keyword_text=kw.keyword, skill_source="local", skill_name="skill",
        prompt_text="prompt", status="completed", output_remote_file_id="output-safe",
        created_by_user_id="actor",
    )
    db.add(row)
    db.commit()
    renewed = KeywordImageService(db).retry(tenant_id="tenant-a", keyword_id=kw.id)
    assert renewed.status == "queued"
    assert db.get(ProcessingJobModel, renewed.processing_job_id).payload_json["replace_outputs"] is True
    assert db.get(ProcessingJobModel, renewed.processing_job_id).max_attempts == 1
    # Deletion is checkpointed by the worker before starting imagegen.
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
    assert "Do **not** create or persist preview" in instructions


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

def test_zero_png_failed_job_can_be_manually_regenerated_as_new_one_attempt_run(db):
    row = keyword(db, "missing-png", "tenant-a", picked=True)
    previous = ProcessingJobModel(
        id="first-processing", tenant_id="tenant-a", job_type="rrugc_keyword_image_generate",
        entity_type="rrugc_keyword_image_job", entity_id="zero-png-job",
        idempotency_key="first-processing", payload_json={"keyword_image_job_id": "zero-png-job"},
        status="failed", max_attempts=1,
    )
    job = RrugcKeywordImageJobModel(
        id="zero-png-job", tenant_id="tenant-a", keyword_id=row.id,
        keyword_text=row.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
        prompt_text="prompt", status="failed", created_by_user_id="actor",
        processing_job_id=previous.id, last_error_code="stage1_six_outputs_invalid",
        last_error_message="Stage 1 returned 0 PNG candidates, 0/6 uniquely numbered finals",
    )
    db.add_all([previous, job])
    db.commit()
    service = KeywordImageService(db)
    renewed = service.regenerate(tenant_id="tenant-a", keyword_id=row.id)
    assert renewed.status == "queued"
    assert renewed.processing_job_id != previous.id
    assert renewed.retry_count == 0
    assert db.get(ProcessingJobModel, previous.id).status == "failed"
    second = db.get(ProcessingJobModel, renewed.processing_job_id)
    assert second.max_attempts == 1
    assert second.payload_json == {"keyword_image_job_id": job.id, "replace_outputs": True}
    assert db.query(ProcessingJobModel).count() == 2


def test_partial_outputs_and_drive_failure_can_be_replaced_only_by_new_run(db):
    row = keyword(db, "part-failed", "tenant-a", picked=True)
    job = RrugcKeywordImageJobModel(
        id="part-failed-job", tenant_id="tenant-a", keyword_id=row.id,
        keyword_text=row.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
        prompt_text="prompt", status="failed", created_by_user_id="actor",
        last_error_code="stage1_six_outputs_invalid",
        last_error_message="Stage 1 returned 5 PNG candidates, missing design numbers 6",
    )
    db.add(job)
    db.commit()
    service = KeywordImageService(db)
    renewed = service.regenerate(tenant_id="tenant-a", keyword_id=row.id)
    assert renewed.status == "queued"
    assert db.get(ProcessingJobModel, renewed.processing_job_id).payload_json["replace_outputs"]
    with pytest.raises(KeywordImageError, match="Only completed or failed"):
        service.regenerate(tenant_id="tenant-a", keyword_id=row.id)
    current = db.get(ProcessingJobModel, renewed.processing_job_id)
    current.status = "failed"
    job.status = "failed"
    job.last_error_code = "managed_storage_temporarily_unavailable"
    db.commit()
    replacement = service.retry(tenant_id="tenant-a", keyword_id=row.id)
    assert replacement.processing_job_id != current.id
    assert db.get(ProcessingJobModel, replacement.processing_job_id).payload_json["replace_outputs"]


def test_safe_publish_cleanup_only_removes_old_versions_after_complete_set(db):
    import asyncio
    from types import SimpleNamespace
    from sqlalchemy.orm import Session
    from app.modules.realistic_review_ugc.keyword_images import KeywordImageGenerateHandler
    from app.domain.providers.contracts import StorageProviderError

    kw = keyword(db, "atomic-publish", "tenant-a", picked=True)
    db.add(RrugcKeywordImageJobModel(
        id="published-job", tenant_id="tenant-a", keyword_id=kw.id,
        keyword_text=kw.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
        prompt_text="prompt", status="running", created_by_user_id="actor",
        output_remote_file_id="old-file-0",
    ))
    db.add_all([
        RrugcImageOutputVersionModel(
            id=f"old-{i}", tenant_id="tenant-a", stage="stage1", job_id="published-job",
            version=i, remote_file_id=f"old-file-{i}", output_name=f"output/final/design_{i:02}.png",
        ) for i in (1, 2)
    ])
    db.add_all([
        RrugcImageOutputVersionModel(
            id=f"new-{i}", tenant_id="tenant-a", stage="stage1", job_id="published-job",
            version=2+i, remote_file_id=f"new-file-{i}", processing_job_id="new-process",
            output_name=f"output/final/design_{i:02}.png",
        ) for i in range(1, 7)
    ])
    db.commit()
    context = SimpleNamespace(
        job=SimpleNamespace(entity_id="published-job", tenant_id="tenant-a"),
        dependencies=SimpleNamespace(session_factory=lambda: Session(db.bind)),
        logger=SimpleNamespace(info=lambda *a, **kw: None),
    )
    deleted = []
    class Storage:
        fail = False
        async def delete_asset(self, entry):
            if self.fail:
                raise StorageProviderError("Drive offline", code="managed_storage_network_error", retryable=True)
            deleted.append(entry.remote_file_id)
    storage = Storage()
    handler = KeywordImageGenerateHandler()
    # Attempted deletion before final publish is prohibited.
    assert asyncio.run(handler._cleanup_replaced_stage1_outputs(
        context, storage, active_processing_id="new-process")) == 0
    assert deleted == []
    db.get(RrugcKeywordImageJobModel, "published-job").status = "completed"
    db.get(RrugcKeywordImageJobModel, "published-job").output_remote_file_id = "new-file-1"
    db.commit()
    storage.fail = True
    with pytest.raises(StorageProviderError):
        asyncio.run(handler._cleanup_replaced_stage1_outputs(
            context, storage, active_processing_id="new-process"))
    assert db.get(RrugcImageOutputVersionModel, "old-1") is not None
    assert db.get(RrugcImageOutputVersionModel, "new-1") is not None
    storage.fail = False
    assert asyncio.run(handler._cleanup_replaced_stage1_outputs(
        context, storage, active_processing_id="new-process")) == 2
    assert deleted == ["old-file-1", "old-file-2"]
    assert db.get(RrugcImageOutputVersionModel, "old-1") is None
    assert db.get(RrugcImageOutputVersionModel, "new-6") is not None
    assert asyncio.run(handler._cleanup_replaced_stage1_outputs(
        context, storage, active_processing_id="new-process")) == 0


def test_retry_removes_only_own_stage1_versions_from_drive_before_new_generation(db):
    import asyncio
    from types import SimpleNamespace
    from sqlalchemy.orm import sessionmaker
    from app.modules.realistic_review_ugc.keyword_images import KeywordImageGenerateHandler

    ours = keyword(db, "replace-ours", "tenant-a", picked=True)
    other = keyword(db, "replace-others", "tenant-b", picked=True)
    rows = [
        RrugcKeywordImageJobModel(
            id="replace-job", tenant_id="tenant-a", keyword_id=ours.id,
            keyword_text=ours.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
            prompt_text="prompt", status="running", created_by_user_id="actor",
            output_remote_file_id="remote-a",
        ),
        RrugcKeywordImageJobModel(
            id="other-job", tenant_id="tenant-b", keyword_id=other.id,
            keyword_text=other.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
            prompt_text="prompt", status="completed", created_by_user_id="other",
            output_remote_file_id="remote-other",
        ),
        RrugcImageOutputVersionModel(id="ours-1", tenant_id="tenant-a", stage="stage1",
                                     job_id="replace-job", version=1, remote_file_id="remote-a"),
        RrugcImageOutputVersionModel(id="ours-2", tenant_id="tenant-a", stage="stage1",
                                     job_id="replace-job", version=2, remote_file_id="remote-b"),
        RrugcImageOutputVersionModel(id="other-1", tenant_id="tenant-b", stage="stage1",
                                     job_id="other-job", version=1, remote_file_id="remote-other"),
    ]
    db.add_all(rows)
    db.commit()
    deleted = []
    class Storage:
        async def delete_asset(self, entry):
            deleted.append((entry.tenant_id, entry.remote_file_id))
    context = SimpleNamespace(
        job=SimpleNamespace(entity_id="replace-job", tenant_id="tenant-a"),
        dependencies=SimpleNamespace(session_factory=sessionmaker(bind=db.get_bind())),
        logger=SimpleNamespace(info=lambda *a, **kw: None),
    )
    handler = KeywordImageGenerateHandler()
    asyncio.run(handler._delete_previous_stage1_outputs(context, Storage()))
    assert deleted == [("tenant-a", "remote-a"), ("tenant-a", "remote-b")]
    assert db.get(RrugcImageOutputVersionModel, "ours-1") is None
    assert db.get(RrugcImageOutputVersionModel, "ours-2") is None
    assert db.get(RrugcImageOutputVersionModel, "other-1") is not None
    assert db.get(RrugcKeywordImageJobModel, "replace-job").output_remote_file_id is None
    asyncio.run(handler._delete_previous_stage1_outputs(context, Storage()))
    assert len(deleted) == 2


def test_retry_drive_cleanup_failure_keeps_pending_versions_and_no_new_images(db):
    import asyncio
    from types import SimpleNamespace
    from sqlalchemy.orm import sessionmaker
    from app.domain.providers.contracts import StorageProviderError
    from app.modules.realistic_review_ugc.keyword_images import KeywordImageGenerateHandler

    kw = keyword(db, "replace-partial", "tenant-a", picked=True)
    db.add_all([
        RrugcKeywordImageJobModel(
            id="replace-partial-job", tenant_id="tenant-a", keyword_id=kw.id,
            keyword_text=kw.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
            prompt_text="prompt", status="running", created_by_user_id="actor",
            output_remote_file_id="remote-first",
        ),
        RrugcImageOutputVersionModel(id="partial-a", tenant_id="tenant-a", stage="stage1",
                                     job_id="replace-partial-job", version=1, remote_file_id="remote-first"),
        RrugcImageOutputVersionModel(id="partial-b", tenant_id="tenant-a", stage="stage1",
                                     job_id="replace-partial-job", version=2, remote_file_id="remote-second"),
    ])
    db.commit()
    class Storage:
        async def delete_asset(self, entry):
            if entry.remote_file_id == "remote-second":
                raise StorageProviderError("Drive unavailable", code="managed_storage_network_error", retryable=True)
    context = SimpleNamespace(
        job=SimpleNamespace(entity_id="replace-partial-job", tenant_id="tenant-a"),
        dependencies=SimpleNamespace(session_factory=sessionmaker(bind=db.get_bind())),
        logger=SimpleNamespace(info=lambda *a, **kw: None),
    )
    with pytest.raises(StorageProviderError) as raised:
        asyncio.run(KeywordImageGenerateHandler()._delete_previous_stage1_outputs(context, Storage()))
    assert raised.value.code == "stage1_previous_images_delete_failed"
    assert db.get(RrugcImageOutputVersionModel, "partial-a") is None
    assert db.get(RrugcImageOutputVersionModel, "partial-b") is not None


def test_retry_legacy_preview_only_is_also_removed(db):
    import asyncio
    from types import SimpleNamespace
    from sqlalchemy.orm import sessionmaker
    from app.modules.realistic_review_ugc.keyword_images import KeywordImageGenerateHandler

    kw = keyword(db, "legacy-preview", "tenant-a", picked=True)
    db.add(RrugcKeywordImageJobModel(
        id="legacy-preview-job", tenant_id="tenant-a", keyword_id=kw.id,
        keyword_text=kw.keyword, skill_source="local", skill_name=DEFAULT_SKILL,
        prompt_text="prompt", status="running", created_by_user_id="actor",
        output_remote_file_id="legacy-remote",
    ))
    db.commit()
    deleted = []
    class Storage:
        async def delete_asset(self, entry):
            deleted.append(entry.remote_file_id)
    context = SimpleNamespace(
        job=SimpleNamespace(entity_id="legacy-preview-job", tenant_id="tenant-a"),
        dependencies=SimpleNamespace(session_factory=sessionmaker(bind=db.get_bind())),
        logger=SimpleNamespace(info=lambda *a, **kw: None),
    )
    asyncio.run(KeywordImageGenerateHandler()._delete_previous_stage1_outputs(context, Storage()))
    assert deleted == ["legacy-remote"]
    assert not db.query(RrugcImageOutputVersionModel).filter_by(job_id="legacy-preview-job").count()
