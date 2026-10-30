"""MVP Stage 2 handoff guards: no publication before reviewed history."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.typography_stage2_handoff import (
    handoff_enabled, select_saved_v2_design, HISTORY_STAGE,
)
from app.modules.realistic_review_ugc.typography_imagegen_pilot import TypographyPilotError
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordVolumeModel, RrugcSourcePlanModel,
)


def flags(**overrides):
    values = dict(
        TYPOGRAPHY_V2_ENABLED=True,
        TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED=True,
        TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED=True,
        TYPOGRAPHY_V2_CANARY_TENANT_IDS="tenant-a",
        TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED=True,
        TYPOGRAPHY_V2_STAGE2_HANDOFF_ENABLED=False,
        MANAGED_ASSET_STORAGE_ENABLED=True,
        IMAGE_GENERATION_ENABLED=True,
        CODEX_IMAGE_GENERATION_ENABLED=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_handoff_fails_closed_until_independent_flag_enabled():
    assert not handoff_enabled(flags(), "tenant-a")
    assert not handoff_enabled(flags(TYPOGRAPHY_V2_STAGE2_HANDOFF_ENABLED=True), "tenant-b")
    assert not handoff_enabled(flags(TYPOGRAPHY_V2_STAGE2_HANDOFF_ENABLED=True,
                                     TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED=False), "tenant-a")
    assert handoff_enabled(flags(TYPOGRAPHY_V2_STAGE2_HANDOFF_ENABLED=True), "tenant-a")


def test_disabled_handoff_has_no_db_writes():
    engine = create_engine("sqlite://")
    with Session(engine) as session:
        with pytest.raises(TypographyPilotError) as exc:
            select_saved_v2_design(
                session, settings=flags(), tenant_id="tenant-a",
                user_id="operator", keyword_id="kw-a", version=1, selected=True,
            )
        assert exc.value.code == "handoff_disabled"
    engine.dispose()


def test_handoff_rejects_invalid_version_and_operator_before_query():
    engine = create_engine("sqlite://")
    with Session(engine) as session:
        for version in (True, 0, -1):
            with pytest.raises(TypographyPilotError) as exc:
                select_saved_v2_design(
                    session, settings=flags(TYPOGRAPHY_V2_STAGE2_HANDOFF_ENABLED=True),
                    tenant_id="tenant-a", user_id="operator",
                    keyword_id="kw-a", version=version, selected=True,
                )
            assert exc.value.code == "handoff_input_invalid"
        with pytest.raises(TypographyPilotError) as exc:
            select_saved_v2_design(
                session, settings=flags(TYPOGRAPHY_V2_STAGE2_HANDOFF_ENABLED=True),
                tenant_id="tenant-a", user_id="", keyword_id="kw-a",
                version=1, selected=True,
            )
        assert exc.value.code == "operator_invalid"
    engine.dispose()
