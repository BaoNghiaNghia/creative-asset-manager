from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.domain.processing.handlers import JobHandlerResult
from app.modules.realistic_review_ugc.creative_brief import CreativeBrief
from app.modules.realistic_review_ugc.creative_brief_service import (
    CreativeBriefGenerateHandler, queue_brief,
)
from app.modules.realistic_review_ugc.model import RrugcKeywordCreativeBriefModel, RrugcKeywordVolumeModel


def test_worker_persists_valid_brief_without_imagegen(monkeypatch, tmp_path):
    engine = create_engine("sqlite://")
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcKeywordCreativeBriefModel.__table__.create(engine)
    provider_job_id = str(uuid4())
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.creative_brief_service.ProcessingRepository.create_job",
        lambda _self, **_kwargs: SimpleNamespace(id=provider_job_id),
    )
    keyword = "SUPPORT LIVE MUSIC HIRE LIVE MUSICIANS"
    keyword_id = str(uuid4())
    with Session(engine) as session:
        session.add(RrugcKeywordVolumeModel(
            id=keyword_id, tenant_id="tenant-test", keyword=keyword,
            keyword_normalized=keyword.lower(), picked=True,
            search_volume=260,
        ))
        session.commit()
        queue_brief(
            session, tenant_id="tenant-test", keyword_id=keyword_id,
            settings=SimpleNamespace(RRUGC_CREATIVE_BRIEF_ENABLED=True),
        )
        session.commit()
        row_id = session.scalar(select(RrugcKeywordCreativeBriefModel.id).where(
            RrugcKeywordCreativeBriefModel.keyword_id == keyword_id))

    concepts = [
        dict(name=f"Concept {mode}", mode=mode, familiar_format="poster",
             creative_twist="interaction", description="visual idea",
             creative_hook=f"hook {mode}", typography_plan="large",
             embroidery_plan="satin", clarity_assessment="clear",
             embroidery_fit="feasible", risks=[])
        for mode in ("safe", "creative_twist", "explore")
    ]
    async def fake_codex(*_args, **_kwargs):
        return CreativeBrief.model_validate(dict(
            keyword_exact=keyword, strategy="identity_first", meaning_emotion="support",
            target_audience="musicians", purchase_motivation="identity",
            creative_hook="cable underline", concepts=concepts))
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.creative_brief_service.generate_brief", fake_codex)

    settings = SimpleNamespace(
        RRUGC_CREATIVE_BRIEF_ENABLED=True,
        IMAGE_GENERATION_STAGING_ROOT=str(tmp_path),
        CODEX_IMAGE_BINARY="codex",
        CODEX_IMAGE_HOME=str(tmp_path),
        RRUGC_CREATIVE_BRIEF_MODEL="",
        RRUGC_CREATIVE_BRIEF_TIMEOUT_SECONDS=60,
    )
    context = SimpleNamespace(
        is_cancelled=False,
        job=SimpleNamespace(id=provider_job_id, entity_id=row_id, tenant_id="tenant-test"),
        dependencies=SimpleNamespace(session_factory=lambda: Session(engine)),
    )
    result = CreativeBriefGenerateHandler(settings)(context)
    assert isinstance(result, JobHandlerResult)
    assert result == JobHandlerResult.completed()

    with Session(engine) as session:
        stored = session.get(RrugcKeywordCreativeBriefModel, row_id)
        assert stored.status == "completed"
        assert stored.brief_json["keyword_exact"] == keyword
        assert len(stored.brief_json["concepts"]) == 3
        assert stored.completed_at is not None
