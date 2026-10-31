from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.creative_brief_service import (
    PROMPT_VERSION, brief_summary, get_brief, queue_brief, queue_picked_backfill,
)
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordCreativeBriefModel, RrugcKeywordVolumeModel,
)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcKeywordCreativeBriefModel.__table__.create(engine)
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.creative_brief_service.ProcessingRepository.create_job",
        lambda _self, **_kwargs: SimpleNamespace(id=str(uuid4())),
    )
    with Session(engine) as session:
        yield session
    engine.dispose()


def settings():
    return SimpleNamespace(RRUGC_CREATIVE_BRIEF_ENABLED=True)


def insert_keyword(session, tenant="tenant-a", word="I CRY A LOT", picked=True):
    row = RrugcKeywordVolumeModel(
        id=str(uuid4()), tenant_id=tenant, keyword=word,
        keyword_normalized=word.lower(), picked=picked,
        search_volume=15,
    )
    session.add(row)
    session.commit()
    return row


def test_queue_picked_and_read_status(db):
    row = insert_keyword(db)
    status, created = queue_brief(db, tenant_id="tenant-a", keyword_id=row.id, settings=settings())
    db.commit()
    assert (status, created) == ("queued", True)
    detail = get_brief(db, tenant_id="tenant-a", keyword_id=row.id)
    assert detail["status"] == "queued"
    stored = db.scalar(select(RrugcKeywordCreativeBriefModel).where(
        RrugcKeywordCreativeBriefModel.keyword_id == row.id))
    assert stored.prompt_version == PROMPT_VERSION
    assert stored.processing_job_id


def test_queue_is_idempotent(db):
    row = insert_keyword(db)
    assert queue_brief(db, tenant_id="tenant-a", keyword_id=row.id, settings=settings())[1]
    assert not queue_brief(db, tenant_id="tenant-a", keyword_id=row.id, settings=settings())[1]


def test_tenant_and_pick_required(db):
    row = insert_keyword(db)
    with pytest.raises(LookupError):
        queue_brief(db, tenant_id="tenant-b", keyword_id=row.id, settings=settings())
    unpicked = insert_keyword(db, word="UNPICKED", picked=False)
    with pytest.raises(LookupError):
        queue_brief(db, tenant_id="tenant-a", keyword_id=unpicked.id, settings=settings())


def test_backfill_only_selected_and_skip_repeat(db):
    selected = insert_keyword(db, word="USED")
    insert_keyword(db, word="NOT USED", picked=False)
    result = queue_picked_backfill(db, tenant_id="tenant-a", settings=settings())
    assert result == {"examined": 1, "queued": 1, "skipped": 0}
    again = queue_picked_backfill(db, tenant_id="tenant-a", settings=settings())
    assert again["queued"] == 0
    assert get_brief(db, tenant_id="tenant-a", keyword_id=selected.id)["status"] == "queued"


def test_backfill_can_retry_failed_only_when_explicit_and_remains_idempotent(db):
    failed = insert_keyword(db, word="FAILED PHRASE")
    ready = insert_keyword(db, word="READY PHRASE")
    foreign = insert_keyword(db, tenant="tenant-b", word="OTHER TENANT")
    assert queue_picked_backfill(db, tenant_id="tenant-a", settings=settings())["queued"] == 2
    failed_row = db.scalar(select(RrugcKeywordCreativeBriefModel).where(
        RrugcKeywordCreativeBriefModel.keyword_id == failed.id,
    ))
    ready_row = db.scalar(select(RrugcKeywordCreativeBriefModel).where(
        RrugcKeywordCreativeBriefModel.keyword_id == ready.id,
    ))
    old_failed_job = failed_row.processing_job_id
    old_ready_job = ready_row.processing_job_id
    failed_row.status = "failed"
    ready_row.status = "completed"
    db.commit()
    # Routine backfills must not silently restart failed analyses.
    assert queue_picked_backfill(db, tenant_id="tenant-a", settings=settings())["queued"] == 0
    retry = queue_picked_backfill(db, tenant_id="tenant-a", settings=settings(), retry_failed=True)
    assert retry == {"examined": 2, "queued": 1, "skipped": 1}
    assert failed_row.status == "queued"
    assert failed_row.processing_job_id != old_failed_job
    assert ready_row.processing_job_id == old_ready_job
    assert queue_picked_backfill(
        db, tenant_id="tenant-a", settings=settings(), retry_failed=True,
    )["queued"] == 0
    assert db.scalar(select(RrugcKeywordCreativeBriefModel).where(
        RrugcKeywordCreativeBriefModel.keyword_id == foreign.id,
    )) is None


def test_backfill_offset_window_walks_all_picked_keywords_once(db):
    selected = [insert_keyword(db, word=f"NUMBER {n}") for n in range(7)]
    queued = []
    for offset in (0, 3, 6):
        result = queue_picked_backfill(
            db, tenant_id="tenant-a", limit=3, offset=offset, settings=settings(),
        )
        queued.append(result["queued"])
    assert queued == [3, 3, 1]
    assert all(get_brief(db, tenant_id="tenant-a", keyword_id=row.id)["status"] == "queued"
               for row in selected)
    assert queue_picked_backfill(db, tenant_id="tenant-a", settings=settings())["queued"] == 0


def test_brief_summary_never_invents_data(db):
    assert brief_summary(None) is None
    row = insert_keyword(db)
    queue_brief(db, tenant_id="tenant-a", keyword_id=row.id, settings=settings())
    stored = db.scalar(select(RrugcKeywordCreativeBriefModel).where(
        RrugcKeywordCreativeBriefModel.keyword_id == row.id))
    summary = brief_summary(stored)
    assert summary["status"] == "queued"
    assert summary["concept_count"] == 0
    assert summary["creative_hook"] is None