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


def test_daily_gemini_copy_migration_follows_current_head_and_is_reversible():
    migration = Path(__file__).resolve().parents[4] / "database/migrations/versions/0069_inventory_daily_gemini_copy.py"
    source = migration.read_text()
    assert 'revision = "0069_inventory_daily_gemini_copy"' in source
    assert 'down_revision = "0068_heavy_video_resource_lane"' in source
    assert '"gemini_file_id"' in source
    assert 'op.drop_column("inventory_daily_sheet_snapshots", "gemini_file_id")' in source


def test_daily_carry_forward_migration_follows_gemini_copy_head():
    migration = Path(__file__).resolve().parents[4] / "database/migrations/versions/0070_inventory_daily_carry_forward.py"
    source = migration.read_text()
    assert 'revision = "0070_inventory_carry_forward"' in source
    assert 'down_revision = "0069_inventory_daily_gemini_copy"' in source
    assert '"inventory_daily_carry_forwards"' in source
    assert '"daily_carry_forward_time_local"' in source


def test_prompt_version_migration_follows_carry_forward_head():
    migration = Path(__file__).resolve().parents[4] / "database/migrations/versions/0071_inventory_prompt_versions.py"
    source = migration.read_text()
    assert 'revision = "0071_inventory_prompt_versions"' in source
    assert 'down_revision = "0070_inventory_carry_forward"' in source
    assert '"inventory_prompt_versions"' in source
    assert 'f"{prefix}_source"' in source


def test_prompt_snapshot_freeze_migration_follows_prompt_versions():
    migration = Path(__file__).resolve().parents[4] / "database/migrations/versions/0072_inventory_prompt_snapshot_freeze.py"
    source = migration.read_text()
    assert 'revision = "0072_prompt_snapshot_freeze"' in source
    assert 'down_revision = "0071_inventory_prompt_versions"' in source
    assert '"gemini_prompt_content"' in source
    assert '"prompt_content"' in source
