"""Enforce a single active RRUGC Pinterest Scout per tenant.

Revision ID: 0106_rrugc_single_scout_agent
Revises: 0105_rrugc_quality_first_real_photos
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0106_rrugc_single_scout_agent"
down_revision = "0105_rrugc_quality_first_real_photos"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_rrugc_scout_agent_one_active_per_tenant"


def _duplicate_agent_ids_sql() -> str:
    return """
        SELECT id
        FROM (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY tenant_id
                    ORDER BY
                        CASE WHEN last_seen_at IS NULL THEN 1 ELSE 0 END,
                        last_seen_at DESC,
                        created_at DESC,
                        id DESC
                ) AS rn
            FROM rrugc_scout_agents
            WHERE active = true
        ) ranked
        WHERE rn > 1
    """


def upgrade() -> None:
    bind = op.get_bind()
    now_sql = "CURRENT_TIMESTAMP"
    duplicates = _duplicate_agent_ids_sql()

    bind.execute(sa.text(f"""
        UPDATE rrugc_scout_runs
        SET status = 'cancelled',
            last_error_code = 'scout_agent_superseded',
            completed_at = {now_sql},
            last_heartbeat_at = {now_sql}
        WHERE status IN ('claimed', 'running')
          AND agent_id IN ({duplicates})
    """))

    bind.execute(sa.text(f"""
        UPDATE rrugc_campaigns
        SET scan_lease_agent_id = NULL,
            scan_lease_run_id = NULL,
            scan_lease_expires_at = NULL,
            scan_next_at = {now_sql},
            scout_status = 'offline'
        WHERE scan_lease_agent_id IN ({duplicates})
    """))

    bind.execute(sa.text(f"""
        UPDATE rrugc_scout_agents
        SET active = false,
            status = 'offline',
            archived_at = {now_sql}
        WHERE id IN ({duplicates})
    """))

    op.create_index(
        INDEX_NAME,
        "rrugc_scout_agents",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("active = true"),
        sqlite_where=sa.text("active = 1"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="rrugc_scout_agents")
