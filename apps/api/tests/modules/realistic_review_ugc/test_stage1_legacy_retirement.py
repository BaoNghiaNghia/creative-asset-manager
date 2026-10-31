"""Stage 1 cuts over to Creative Brief V3; no new Google Fonts jobs."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.keyword_images import (
    KeywordImageError, KeywordImageService, legacy_stage1_generation_allowed,
)


def test_legacy_generator_is_default_off_even_for_old_config_objects():
    assert not legacy_stage1_generation_allowed(SimpleNamespace())
    assert not legacy_stage1_generation_allowed(
        SimpleNamespace(RRUGC_STAGE1_LEGACY_GENERATION_ENABLED=False)
    )
    assert legacy_stage1_generation_allowed(
        SimpleNamespace(RRUGC_STAGE1_LEGACY_GENERATION_ENABLED=True)
    )


@pytest.mark.parametrize("operation", ("queue", "queue_manual", "regenerate", "retry"))
def test_all_legacy_new_job_entrypoints_are_retired_before_any_database_write(operation):
    engine = create_engine("sqlite://")
    try:
        with Session(engine) as session:
            service = KeywordImageService(session, SimpleNamespace(
                RRUGC_STAGE1_LEGACY_GENERATION_ENABLED=False,
            ))
            call = {
                "queue": lambda: service.queue(
                    tenant_id="tenant-a", user_id="actor", keyword_id="keyword-a",
                    skill_name="concept-short-v1",
                ),
                "queue_manual": lambda: service.queue_manual(
                    tenant_id="tenant-a", user_id="actor", text="CREATIVE DICTATOR",
                    skill_name="gatorhats-typography-concepts",
                ),
                "regenerate": lambda: service.regenerate(
                    tenant_id="tenant-a", keyword_id="keyword-a",
                    skill_name="concept-short-v1",
                ),
                "retry": lambda: service.retry(
                    tenant_id="tenant-a", keyword_id="keyword-a",
                ),
            }[operation]
            with pytest.raises(KeywordImageError) as ex:
                call()
            assert ex.value.code == "stage1_legacy_generation_retired"
            assert ex.value.status_code == 410
    finally:
        engine.dispose()
