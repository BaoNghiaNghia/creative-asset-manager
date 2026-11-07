"""Add inert, tenant-scoped holdout study registrations and keyword reservations.

Revision ID: 0204_cam_agent_holdout_studies
Revises: 0203_cam_agent_harness_store

No backfill, trial activation, output mutation, or automatic learning.
The prior 0203 backend has a structural rollback bridge for this exact schema.
"""
from alembic import op
import sqlalchemy as sa

revision = "0204_cam_agent_holdout_studies"
down_revision = "0203_cam_agent_harness_store"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cam_agent_trials",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("study_id", sa.String(64), nullable=False),
        sa.Column("cohort", sa.String(20), nullable=False),
        sa.Column("candidate_event_key", sa.String(64), nullable=False),
        sa.Column("agent_version", sa.String(20), nullable=False),
        sa.Column("trial_version", sa.String(32), nullable=False),
        sa.Column("holdout_percent", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="registered"),
        sa.Column("candidate_source_digest", sa.String(64), nullable=False),
        sa.Column("registered_by", sa.String(255), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("tenant_id", "study_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_event_key"],
            ["cam_agent_experiences.tenant_id", "cam_agent_experiences.event_key"],
            name="fk_cam_trial_candidate", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("holdout_percent BETWEEN 10 AND 40", name="ck_cam_trial_percent"),
        sa.CheckConstraint("status IN ('registered', 'cancelled')", name="ck_cam_trial_status"),
    )
    op.create_index(
        "ix_cam_trials_tenant_cohort", "cam_agent_trials",
        ["tenant_id", "cohort", "registered_at"],
    )
    op.create_table(
        "cam_agent_trial_assignments",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("study_id", sa.String(64), nullable=False),
        sa.Column("keyword_id", sa.String(36), nullable=False),
        sa.Column("keyword_sha256", sa.String(64), nullable=False),
        sa.Column("arm", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="reserved"),
        sa.Column("assigned_by", sa.String(255), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("tenant_id", "study_id", "keyword_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "study_id"],
            ["cam_agent_trials.tenant_id", "cam_agent_trials.study_id"],
            name="fk_cam_assignment_trial", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["keyword_id"], ["rrugc_keyword_volumes.id"],
            name="fk_cam_assignment_keyword", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "keyword_id", name="uq_cam_assignment_tenant_keyword",
        ),
        sa.CheckConstraint("arm IN ('control', 'challenger')", name="ck_cam_assignment_arm"),
        sa.CheckConstraint("status IN ('reserved', 'exposed')", name="ck_cam_assignment_status"),
    )
    op.create_index(
        "ix_cam_assignments_trial_arm", "cam_agent_trial_assignments",
        ["tenant_id", "study_id", "arm"],
    )


def downgrade():
    # Never silently delete a registered experiment or an existing reservation.
    # Production rollback is code-only, using the reviewed 0203 compatibility bridge.
    bind = op.get_bind()
    if (
        bind.scalar(sa.text("SELECT COUNT(*) FROM cam_agent_trial_assignments"))
        or bind.scalar(sa.text("SELECT COUNT(*) FROM cam_agent_trials"))
    ):
        raise RuntimeError("Refusing destructive holdout study downgrade with data")
    op.drop_index("ix_cam_assignments_trial_arm", table_name="cam_agent_trial_assignments")
    op.drop_table("cam_agent_trial_assignments")
    op.drop_index("ix_cam_trials_tenant_cohort", table_name="cam_agent_trials")
    op.drop_table("cam_agent_trials")
