from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
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



def test_ecommerce_history_serializes_numpy_perspective_coordinates_without_losing_types():
    """Regression: Slot 2 previously failed AFTER PNG upload with np.int64 JSONB."""
    engine = create_engine("sqlite://")
    RrugcImageOutputVersionModel.__table__.create(engine)
    with Session(engine) as session:
        job = SimpleNamespace(id="slot-2-job", output_remote_file_id=None)
        version = save_output_version(
            session, tenant_id="tenant-a", stage="ecommerce", job=job,
            remote_file_id="uploaded-3q-png", content_type="image/png",
            size_bytes=12345, width=900, height=1000, processing_job_id="attempt-3q",
            font_info={
                "thread_inpaint_pipeline": {
                    "version": "threadtransfer-v4-calibrated-quad",
                    "transfer_box": [np.int64(550), np.int64(312),
                                     np.int64(795), np.int64(552)],
                    "accepted_thread_pixels": np.int32(1987),
                    "normal": np.float32(.75),
                    "qa_passed": np.bool_(True),
                    "example_vector": np.array([np.int64(1), np.int64(2)]),
                },
            },
        )
        session.commit()
        assert version == 1
        history = output_versions(session, tenant_id="tenant-a", stage="ecommerce", job=job)
        details = history[0]["font_info"]["thread_inpaint_pipeline"]
        assert details["transfer_box"] == [550, 312, 795, 552]
        assert all(type(value) is int for value in details["transfer_box"])
        assert details["accepted_thread_pixels"] == 1987
        assert details["qa_passed"] is True
        assert details["example_vector"] == [1, 2]
        assert abs(details["normal"] - .75) < 1e-6
    engine.dispose()


def test_history_rejects_arbitrary_metadata_objects_not_stringifying_them():
    with pytest.raises(TypeError, match="Unsupported output metadata"):
        from app.modules.realistic_review_ugc.output_versions import _json_safe_output_metadata
        _json_safe_output_metadata({"invalid": object()})


def test_default_skill_isolated_by_tenant_and_stage(monkeypatch):
    # This test covers tenant/stage scoping, not deployment of actual Skill ZIPs.
    monkeypatch.setattr("app.modules.realistic_review_ugc.stage_skill_settings.keyword_skill_compatible", lambda name: True)
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
            "stage1": "local:keyword", "stage2": "local:scale", "stage4": "local:ugc",
            "prod_s1": "local:keyword", "prod_s2_4": "local:scale", "prod_s2_col": "local:scale",
            "rev_s2": "local:scale", "rev_s4": "local:ugc",
        }
        assert stage_skill_defaults(session, tenant_id="tenant-b") == {
            "stage1": "local:other", "prod_s1": "local:other",
        }