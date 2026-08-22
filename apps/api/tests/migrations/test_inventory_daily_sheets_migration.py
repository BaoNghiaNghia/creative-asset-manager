from pathlib import Path


def test_inventory_daily_sheet_migration_is_single_head_successor():
    migration = Path(__file__).resolve().parents[4] / "database/migrations/versions/0050_inventory_daily_sheets.py"
    source = migration.read_text()
    assert 'revision = "0050_inventory_daily_sheets"' in source
    assert 'down_revision = "0049_video_analysis_persistence"' in source
    assert '"inventory_daily_sheet_snapshots"' in source
    assert '"inventory_daily_sheet_reconciliations"' in source
    assert "uq_inventory_sheet_snapshot_tenant_date" in source
    assert "uq_inventory_sheet_reconcile_tenant_date" in source
    assert "image_pipeline_enabled" in source
    assert "daily_sheet_automation_enabled" in source
