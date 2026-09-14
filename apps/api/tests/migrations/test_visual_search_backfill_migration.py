from pathlib import Path


def test_visual_search_backfill_migration_follows_head_and_is_reversible():
    path=Path(__file__).resolve().parents[4]/"database/migrations/versions/0074_visual_search_backfill_runs.py"
    source=path.read_text()
    assert 'revision = "0074_visual_backfill_runs"' in source
    assert 'down_revision = "0073_same_day_lifecycle"' in source
    assert '"visual_search_backfill_runs"' in source
    assert 'def downgrade()' in source
