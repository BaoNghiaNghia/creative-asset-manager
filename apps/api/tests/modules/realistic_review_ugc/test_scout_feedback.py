"""Manual Scout feedback remains tenant-safe and shared by all desktop agents."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcKeywordVolumeModel, RrugcScoutFeedbackModel,
)
from app.modules.realistic_review_ugc.scout_feedback import (
    blocked_targets, claim_priority, finish_priority, renew_priority,
    statuses_for_rows, update_feedback,
)


def test_scout_metrics_include_keyword_totals_and_review_runs():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.modules.realistic_review_ugc.router import list_scout_metrics
    from app.modules.realistic_review_ugc.model import (
        RrugcScoutMetricCycleModel, RrugcScoutRunModel,
    )

    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        RrugcKeywordVolumeModel.__table__,
        RrugcScoutFeedbackModel.__table__,
        RrugcScoutMetricCycleModel.__table__,
        RrugcScoutRunModel.__table__,
    ):
        table.create(engine)
    try:
        with Session(engine) as session:
            now = datetime.now(timezone.utc)
            session.add_all([
                RrugcKeywordVolumeModel(
                    tenant_id="tenant-a", keyword="Cowboy Hat", keyword_normalized="cowboy hat",
                    created_at=now,
                ),
                RrugcKeywordVolumeModel(
                    tenant_id="tenant-b", keyword="Houston Astros", keyword_normalized="houston astros",
                    created_at=now,
                ),
                RrugcScoutMetricCycleModel(
                    tenant_id="tenant-a", agent_id="agent-1", mode="keyword",
                    machine_label="PC-A", cycle_id="cycle-id-12345",
                    scanned_pins=12, found_quotes=7, new_keywords=5,
                    duplicate_pins=3, errors=0, created_at=now,
                ),
                RrugcScoutRunModel(
                    tenant_id="tenant-a", agent_id="agent-1", campaign_id="c1",
                    status="completed", query="trucker hat",
                    target_count=10, max_scroll_batches=2, auto_import=True,
                    submitted_count=9, created_count=5, existing_count=4, started_at=now,
                ),
            ])
            session.commit()
            result = list_scout_metrics(
                session=session, principal=SimpleNamespace(active_tenant_id="tenant-a"),
            )
            assert result["overview"]["total_keywords"] == 1
            assert result["overview"]["added_24h"] == 1
            assert result["items"][0]["machine_label"] == "PC-A"
            assert result["items"][0]["new_keywords"] == 5
            assert result["review_items"][0]["submitted"] == 9
            assert result["review_items"][0]["new_references"] == 5
            assert result["review_items"][0]["duplicates"] == 4
    finally:
        engine.dispose()


def test_keyword_and_pin_feedback_are_independently_reversible():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcScoutFeedbackModel.__table__.create(engine)
    try:
        with Session(engine) as session:
            row = RrugcKeywordVolumeModel(
                tenant_id="tenant-a", keyword="Cowboy Hat",
                keyword_normalized="cowboy hat", source_pin_url="https://pinterest.com/pin/123/",
                source_image_url="https://i.pinimg.com/originals/aa.jpg",
            )
            session.add(row)
            session.commit()

            update_feedback(session, row, "blocked", "pin", "user-a")
            session.commit()
            assert blocked_targets(session, "tenant-a") == {
                "blocked_keywords": [], "blocked_pins": ["https://www.pinterest.com/pin/123/"],
            }
            assert blocked_targets(session, "tenant-b") == {
                "blocked_keywords": [], "blocked_pins": [],
            }
            statuses = statuses_for_rows(session, "tenant-a", [row])
            assert statuses[("pin", "https://www.pinterest.com/pin/123/")] == "blocked"

            update_feedback(session, row, "neutral", "pin", "user-a")
            update_feedback(session, row, "suggested", "keyword", "user-a")
            session.commit()
            assert blocked_targets(session, "tenant-a")["blocked_pins"] == []
            claim = claim_priority(session, "tenant-a", "agent-one")
            assert claim is not None and claim["type"] == "keyword"
            assert claim["keyword"] == "Cowboy Hat"
            assert claim_priority(session, "tenant-a", "agent-two") is None
            assert renew_priority(session, "tenant-a", "agent-two", claim["id"], claim["lease_token"]) is False
            assert renew_priority(session, "tenant-a", "agent-one", claim["id"], "bad-lease-token") is False
            assert renew_priority(session, "tenant-a", "agent-one", claim["id"], claim["lease_token"]) is True
            assert claim_priority(session, "tenant-a", "agent-two") is None
            assert finish_priority(session, "tenant-a", "agent-two", claim["id"], claim["lease_token"], True) is False
            assert finish_priority(session, "tenant-a", "agent-one", claim["id"], claim["lease_token"], True) is True
            assert claim_priority(session, "tenant-a", "agent-two") is None

            update_feedback(session, row, "suggested", "keyword", "user-a")
            session.commit()
            again = claim_priority(session, "tenant-a", "agent-two")
            assert again is not None and again["id"] == claim["id"]
    finally:
        engine.dispose()

def test_blocked_keyword_disappears_from_stage0_only_after_grace_period():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from app.modules.realistic_review_ugc.router import list_keyword_analysis

    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcScoutFeedbackModel.__table__.create(engine)
    try:
        with Session(engine) as session:
            row = RrugcKeywordVolumeModel(
                tenant_id="tenant-a", keyword="Houston Astros",
                keyword_normalized="houston astros",
                source_pin_url="https://www.pinterest.com/pin/123/",
            )
            session.add(row)
            session.commit()

            def visible_rows():
                return list_keyword_analysis(
                    page=1, page_size=20, query="", usage="all", tail="all",
                    favorites_only=False, sort_by="search_volume", sort_dir="desc",
                    session=session,
                    principal=SimpleNamespace(active_tenant_id="tenant-a"),
                )

            assert visible_rows().total == 1
            update_feedback(session, row, "blocked", "keyword", "user-a")
            session.commit()
            assert visible_rows().total == 1
            record = session.query(RrugcScoutFeedbackModel).one()
            record.updated_at = datetime.now(timezone.utc) - timedelta(seconds=11)
            session.commit()
            result = visible_rows()
            assert result.total == 0 and result.overview.total_keywords == 0
            assert result.items == []
            update_feedback(session, row, "neutral", "keyword", "user-a")
            session.commit()
            assert visible_rows().total == 1
    finally:
        engine.dispose()


def test_stage0_trademark_sort_and_500_row_limit_are_tenant_safe():
    from inspect import signature
    from types import SimpleNamespace
    from app.modules.realistic_review_ugc.router import list_keyword_analysis

    field = signature(list_keyword_analysis).parameters["page_size"].default
    assert any(getattr(item, "le", None) == 500 for item in field.metadata)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcScoutFeedbackModel.__table__.create(engine)
    try:
        with Session(engine) as session:
            session.add_all([
                RrugcKeywordVolumeModel(
                    tenant_id="tenant-a", keyword=keyword, keyword_normalized=keyword.casefold(),
                    trademark_status=status,
                ) for keyword, status in [
                    ("Northern Stars", "no_exact_match"),
                    ("Houston Astros", "possible_match"),
                    ("Sunset Vibes", "unverified"),
                ]
            ])
            session.add(RrugcKeywordVolumeModel(
                tenant_id="tenant-b", keyword="Hidden Brand", keyword_normalized="hidden brand",
                trademark_status="possible_match",
            ))
            session.commit()
            result = list_keyword_analysis(
                page=1, page_size=500, query="", usage="all", tail="all", favorites_only=False,
                sort_by="trademark", sort_dir="desc", session=session,
                principal=SimpleNamespace(active_tenant_id="tenant-a"),
            )
            assert result.total == 3
            assert result.page_size == 500
            assert [row.keyword for row in result.items] == [
                "Houston Astros", "Sunset Vibes", "Northern Stars",
            ]
            assert result.items[1].trademark_status == "unverified"
            assert result.items[1].trademark_checked_at is None
            assert all(row.keyword != "Hidden Brand" for row in result.items)
    finally:
        engine.dispose()


def test_tm_migration_backfills_all_existing_rows_without_guessing_clearance():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text as sql_text

    migration_path = (
        Path(__file__).resolve().parents[5] / "database" / "migrations" /
        "versions" / "0140_rrugc_keyword_trademark.py"
    )
    spec = spec_from_file_location("tm_migration_test", migration_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sql_text("""
            CREATE TABLE rrugc_keyword_volumes (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                keyword VARCHAR(500) NOT NULL
            )
        """))
        connection.execute(sql_text("""
            INSERT INTO rrugc_keyword_volumes (id, tenant_id, keyword)
            VALUES ('a', 'tenant-a', 'Houston Astros'), ('b', 'tenant-b', 'Sunset Vibes')
        """))
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        rows = connection.execute(sql_text("""
            SELECT id, trademark_status, trademark_checked_at, trademark_source,
                   trademark_match_count FROM rrugc_keyword_volumes ORDER BY id
        """)).all()
        assert len(rows) == 2
        assert all(row[1] == "unverified" and row[2] is None and row[3] is None and row[4] is None for row in rows)
        connection.execute(sql_text("""
            INSERT INTO rrugc_keyword_volumes (id, tenant_id, keyword)
            VALUES ('c', 'tenant-a', 'New Keyword')
        """))
        assert connection.scalar(sql_text("""
            SELECT trademark_status FROM rrugc_keyword_volumes WHERE id = 'c'
        """)) == "unverified"
    engine.dispose()
