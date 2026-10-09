from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcStage2SkillRegistryModel, RrugcStageSkillDefaultModel,
)
from app.modules.realistic_review_ugc.output_versions import save_output_version, output_versions
from app.modules.realistic_review_ugc.stage_skill_settings import stage_skill_defaults


def test_output_generations_preserve_v1_and_record_v2():
    engine = create_engine("sqlite://")
    RrugcImageOutputVersionModel.__table__.create(engine)
    with Session(engine) as session:
        job = SimpleNamespace(id="job-1", output_remote_file_id=None, output_content_type=None,
                              output_size_bytes=None, output_width=None, output_height=None,
                              completed_at=None)
        v1 = save_output_version(session, tenant_id="tenant-a", stage="stage1", job=job,
                                 remote_file_id="file-v1", content_type="image/png", size_bytes=100,
                                 width=512, height=512, processing_job_id="run-1")
        job.output_remote_file_id = "file-v1"
        job.output_content_type = "image/png"
        job.output_size_bytes = 100
        job.output_width = job.output_height = 512
        v2 = save_output_version(session, tenant_id="tenant-a", stage="stage1", job=job,
                                 remote_file_id="file-v2", content_type="image/png", size_bytes=110,
                                 width=600, height=600, processing_job_id="run-2")
        session.commit()
        history = output_versions(session, tenant_id="tenant-a", stage="stage1", job=job)
        assert (v1, v2) == (1, 2)
        assert [item["remote_file_id"] for item in history] == ["file-v2", "file-v1"]



def test_default_skill_isolated_by_tenant_and_stage():
    engine = create_engine("sqlite://")
    RrugcStage2SkillRegistryModel.__table__.create(engine)
    RrugcStageSkillDefaultModel.__table__.create(engine)
    with Session(engine) as session:
        for tenant, stage, key in [
            ("tenant-a", "stage1", "keyword"), ("tenant-a", "stage2", "scale"),
            ("tenant-a", "stage4", "ugc"), ("tenant-b", "stage1", "other"),
        ]:
            row = RrugcStage2SkillRegistryModel(
                id=tenant + "-" + stage, tenant_id=tenant, source="local",
                skill_name=key, display_name=key, description="",
                note="", enabled=True, validation_status="valid",
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.add(RrugcStageSkillDefaultModel(
                tenant_id=tenant, stage=stage, registry_id=row.id,
                updated_at=datetime.now(timezone.utc),
            ))
        session.commit()
        assert stage_skill_defaults(session, tenant_id="tenant-a") == {
            "stage1": "local:keyword", "stage2": "local:scale", "stage4": "local:ugc"}
        assert stage_skill_defaults(session, tenant_id="tenant-b") == {"stage1": "local:other"}
