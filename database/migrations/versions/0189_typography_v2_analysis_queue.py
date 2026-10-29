"""Add G3 tenant-isolated durable Typography analysis queue.

Revision ID: 0189_typography_v2_analysis_queue
Revises: 0188_typography_v2_foundation

Additive only. No jobs are created or activated by this migration.
"""
from alembic import op
import sqlalchemy as sa

revision = "0189_typography_v2_analysis_queue"
down_revision = "0188_typography_v2_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rrugc_typography_analysis_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("reference_id", sa.String(36), nullable=False),
        sa.Column("analyzer_version", sa.String(64), nullable=False),
        sa.Column("image_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(80)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reference_id"],
            ["rrugc_typography_references.tenant_id", "rrugc_typography_references.id"],
            name="fk_typography_analysis_job_reference_tenant", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "reference_id", "analyzer_version", "image_hash",
            name="uq_typography_analysis_job_version",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','needs_review')",
            name="ck_typography_analysis_job_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 3",
            name="ck_typography_analysis_job_attempts",
        ),
    )
    op.create_index(
        "ix_typography_analysis_job_claim", "rrugc_typography_analysis_jobs",
        ["tenant_id", "status", "next_attempt_at", "created_at"],
    )
    op.create_index(
        "ix_typography_analysis_job_lease", "rrugc_typography_analysis_jobs",
        ["tenant_id", "status", "lease_expires_at"],
    )


def downgrade():
    # Prevent accidental removal of queued work or audit/lease history.
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM rrugc_typography_analysis_jobs")
    ):
        raise RuntimeError("typography_analysis_jobs_must_be_drained_before_downgrade")
    op.drop_index("ix_typography_analysis_job_lease", table_name="rrugc_typography_analysis_jobs")
    op.drop_index("ix_typography_analysis_job_claim", table_name="rrugc_typography_analysis_jobs")
    op.drop_table("rrugc_typography_analysis_jobs")
